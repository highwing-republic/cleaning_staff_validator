"""月間勤務条件入力画面（T64〜T65）."""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src.models import MonthlyConditionInput
from src.month_utils import round_half_up_workdays
from src.ui_common import open_connection, select_year_month
from src.validation import validate_monthly_condition

st.set_page_config(page_title="月間勤務条件", layout="wide")
st.title("② 月間勤務条件入力")

conn = open_connection()

year_month = select_year_month()

staff_list = repo.list_staff(conn, include_inactive=False)
if not staff_list:
    st.info("有効なスタッフが登録されていません。先にスタッフを登録してください。")
    st.stop()

existing = {c.staff_id: c for c in repo.get_monthly_conditions(conn, year_month)}

rows = []
for s in staff_list:
    cond = existing.get(s.staff_id)
    target = cond.target_monthly_minutes if cond else 0
    rows.append(
        {
            "staff_id": s.staff_id,
            "スタッフ名": s.staff_name,
            "所定(分)": target,
            "最低(分)": cond.min_monthly_minutes if cond else None,
            "最大(分)": cond.max_monthly_minutes if cond else None,
            "前月連勤": cond.carryover_consecutive_days if cond else 0,
            "目標日数": round_half_up_workdays(target, s.daily_work_minutes) if target else 0,
        }
    )

df = pd.DataFrame(rows)

st.caption("「所定(分)」を変更すると保存後に目標日数が再計算されます。最低・最大は未設定でも構いません。")

edited = st.data_editor(
    df,
    hide_index=True,
    width="stretch",
    disabled=["staff_id", "スタッフ名", "目標日数"],
    column_config={
        "staff_id": None,
        "所定(分)": st.column_config.NumberColumn(min_value=0, step=15, required=True),
        "最低(分)": st.column_config.NumberColumn(min_value=0, step=15),
        "最大(分)": st.column_config.NumberColumn(min_value=0, step=15),
        "前月連勤": st.column_config.NumberColumn(min_value=0, step=1, required=True),
        "目標日数": st.column_config.NumberColumn(disabled=True),
    },
    key="monthly_conditions_editor",
)

if st.button("保存", type="primary"):
    staff_by_id = {s.staff_id: s for s in staff_list}
    conditions: list[MonthlyConditionInput] = []
    all_errors = []

    for _, row in edited.iterrows():
        staff_id = int(row["staff_id"])
        staff = staff_by_id[staff_id]

        def _clean_int(value):
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return None
            return int(value)

        target_val = _clean_int(row["所定(分)"])
        cond = MonthlyConditionInput(
            staff_id=staff_id,
            year_month=year_month,
            target_monthly_minutes=target_val if target_val is not None else 0,
            min_monthly_minutes=_clean_int(row["最低(分)"]),
            max_monthly_minutes=_clean_int(row["最大(分)"]),
            carryover_consecutive_days=_clean_int(row["前月連勤"]) or 0,
        )
        errors = validate_monthly_condition(cond, staff.max_consecutive_days)
        if errors:
            for e in errors:
                all_errors.append((staff.staff_name, e))
        else:
            conditions.append(cond)

    if all_errors:
        for name, e in all_errors:
            st.error(f"{name}さん: {e.message}")
    else:
        for cond in conditions:
            repo.save_monthly_condition(conn, cond)
        st.success("保存しました。")
        st.rerun()
