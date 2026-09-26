"""日別最低人数・ロール入力画面（T68〜T71）."""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src.models import DailyRequirementInput, RoleRequirementInput
from src.month_utils import get_month_dates, weekday_index
from src.requirements_import import (
    ImportFormatError,
    normalize_requirements,
    read_csv_bytes,
    read_excel_bytes,
)
from src.ui_common import WEEKDAY_LABELS_JA, open_connection, select_year_month
from src.validation import validate_daily_requirement

st.set_page_config(page_title="日別最低人数入力", layout="wide")
st.title("④ 日別最低人数・ロール入力")
st.caption(
    "最低人数＝これより少なくしない人数。所定勤務日数に合わせて最大人数まで増えることがあります。0＝休館日。"
)

conn = open_connection()

year_month = select_year_month()

roles = repo.list_roles(conn)
role_col_names = {r["role_id"]: r["role_name"] for r in roles}

dates = get_month_dates(year_month)
existing_req = {r.work_date: r for r in repo.get_daily_requirements(conn, year_month)}
existing_role_req = {
    (r.work_date, r.role_id): r.required_count for r in repo.get_role_requirements(conn, year_month)
}

# ---------------------------------------------------------------------------
# 一括編集グリッド
# ---------------------------------------------------------------------------

st.subheader("一括編集")

rows = []
for d in dates:
    req = existing_req.get(d)
    row = {
        "日付": d,
        "曜日": WEEKDAY_LABELS_JA[weekday_index(d)],
        "稼働率": req.occupancy_rate if req else None,
        "最低人数": req.required_total_staff if req else 0,
        "最大人数": req.max_total_staff if req else None,
        "備考": req.note if req else None,
        "未保存": req is None,
    }
    for role in roles:
        row[role["role_name"]] = existing_role_req.get((d, role["role_id"]), 0)
    rows.append(row)

df = pd.DataFrame(rows)
if df["未保存"].any():
    st.warning(f"未保存の日付が{int(df['未保存'].sum())}件あります（最低人数は既定値0で表示）。")

column_config = {
    "日付": st.column_config.TextColumn(disabled=True),
    "曜日": st.column_config.TextColumn(disabled=True),
    "稼働率": st.column_config.NumberColumn(min_value=0.0, step=0.05),
    "最低人数": st.column_config.NumberColumn(min_value=0, step=1, required=True),
    "最大人数": st.column_config.NumberColumn(min_value=0, step=1),
    "備考": st.column_config.TextColumn(),
    "未保存": None,
}
for role in roles:
    column_config[role["role_name"]] = st.column_config.NumberColumn(min_value=0, step=1)

edited = st.data_editor(
    df,
    hide_index=True,
    width="stretch",
    disabled=["日付", "曜日"],
    column_config=column_config,
    key="requirements_editor",
)

if st.button("保存", type="primary"):
    daily_reqs: list[DailyRequirementInput] = []
    role_reqs: list[RoleRequirementInput] = []
    all_errors = []

    def _clean_int(value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return int(value)

    def _clean_float(value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return float(value)

    for _, row in edited.iterrows():
        work_date = row["日付"]
        row_role_reqs = [
            RoleRequirementInput(work_date, role["role_id"], _clean_int(row[role["role_name"]]) or 0)
            for role in roles
        ]
        req = DailyRequirementInput(
            work_date=work_date,
            required_total_staff=_clean_int(row["最低人数"]) or 0,
            max_total_staff=_clean_int(row["最大人数"]),
            occupancy_rate=_clean_float(row["稼働率"]),
            note=(row["備考"] or None) if isinstance(row["備考"], str) else None,
        )
        errors = validate_daily_requirement(req, row_role_reqs)
        if errors:
            for e in errors:
                all_errors.append((work_date, e))
        else:
            daily_reqs.append(req)
            role_reqs.extend(row_role_reqs)

    if all_errors:
        for work_date, e in all_errors:
            st.error(f"{work_date}: {e.message}")
    else:
        repo.save_daily_requirements(conn, daily_reqs)
        repo.save_role_requirements(conn, role_reqs)
        st.success("保存しました。")
        st.rerun()

# ---------------------------------------------------------------------------
# CSV / Excel 取り込み
# ---------------------------------------------------------------------------

st.subheader("CSV / Excel 取り込み")
st.caption(
    "列: 日付, 稼働率(任意), 最低人数, 最大人数(任意), ロール別必要人数(role_codeまたはロール名, 任意), 備考(任意)。"
    "全件エラーがない場合のみ保存します。"
)

uploaded = st.file_uploader("ファイルを選択", type=["csv", "xlsx"], key="requirements_upload")

if uploaded is not None:
    data = uploaded.getvalue()
    try:
        if uploaded.name.lower().endswith(".xlsx"):
            import_df = read_excel_bytes(data)
        else:
            import_df = read_csv_bytes(data)
    except ImportFormatError as exc:
        st.error(str(exc))
    else:
        imported_daily, imported_role, import_errors = normalize_requirements(
            import_df, year_month, roles
        )
        if import_errors:
            st.error(f"{len(import_errors)}件のエラーがあります。全件成功時のみ保存します。")
            for e in import_errors:
                st.error(e.message)
        else:
            st.success(f"{len(imported_daily)}件を読み込みました。内容を確認して保存してください。")
            preview = pd.DataFrame(
                [
                    {
                        "日付": r.work_date,
                        "稼働率": r.occupancy_rate,
                        "最低人数": r.required_total_staff,
                        "最大人数": r.max_total_staff,
                        "備考": r.note,
                    }
                    for r in imported_daily
                ]
            )
            st.dataframe(preview, width="stretch", hide_index=True)
            if st.button("取り込み内容を保存", key="save_import"):
                repo.save_daily_requirements(conn, imported_daily)
                repo.save_role_requirements(conn, imported_role)
                st.success("保存しました。")
                st.rerun()
