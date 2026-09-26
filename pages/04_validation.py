"""日別検証画面.

ACTIVEな勤怠取込と日別必要条件を比較し、清掃人数・ロール・スキルの不足や判定不能を日別に表示する。
月間検証結果はExcel（月間検証・問題一覧・取込情報）でダウンロードできる。
"""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src import services
from src.constants import (
    VALIDATION_STATUS_ERROR,
    VALIDATION_STATUS_OK,
    VALIDATION_STATUS_WARNING,
)
from src.models import DailyStaffingResult
from src.ui_common import format_date_ja, format_year_month_ja, open_connection
from src.validation_display import (
    REQUIREMENT_MISSING_LABEL,
    format_role_cell,
    format_skill_cell,
)
from src.validation_excel import export_validation_excel, validation_excel_filename

STATUS_LABELS = {
    VALIDATION_STATUS_OK: "🟢 OK",
    VALIDATION_STATUS_WARNING: "🟡 WARNING",
    VALIDATION_STATUS_ERROR: "🔴 ERROR",
}

FILTER_ALL = "全日"
FILTER_PROBLEMS = "問題のある日だけ"
FILTER_ERRORS = "ERRORのみ"
FILTERS = (FILTER_ALL, FILTER_PROBLEMS, FILTER_ERRORS)

st.set_page_config(page_title="日別検証", layout="wide")
st.title("④ 日別検証")
st.caption(
    "取り込んだ勤怠シフト（有効版）を日別必要条件と比較します。"
    "🟡 WARNING は勤務区分不明・未登録スタッフ・要件未設定のため確定できない日、"
    "🔴 ERROR はそれらを最大限見込んでも不足・超過する日です。"
)

conn = open_connection()

active_imports = repo.list_active_imports(conn)  # 対象月の新しい順
if not active_imports:
    st.info("勤怠シフトが取り込まれていません。「② 勤怠CSV取込」から取り込んでください。")
    st.stop()

year_month = st.selectbox(
    "対象年月",
    options=[a.year_month for a in active_imports],
    format_func=format_year_month_ja,
    key="validation_year_month",
)

result = services.validate_month(conn, year_month)
if not result.has_import:
    st.info(services.NO_ACTIVE_IMPORT_MESSAGE)
    st.stop()

role_names = services.get_role_names(conn)

# ---------------------------------------------------------------------------
# 月間サマリー
# ---------------------------------------------------------------------------

st.markdown(
    f"**取込ファイル**: {result.import_record.source_filename}"
    f"（取込日時 {result.import_record.imported_at}）"
)
cols = st.columns(5)
cols[0].metric("対象月", format_year_month_ja(year_month))
cols[1].metric("OK日数", result.count_status(VALIDATION_STATUS_OK))
cols[2].metric("WARNING日数", result.count_status(VALIDATION_STATUS_WARNING))
cols[3].metric("ERROR日数", result.count_status(VALIDATION_STATUS_ERROR))
cols[4].metric("要件未設定日数", result.requirement_missing_days)

try:
    excel_bytes = export_validation_excel(result, role_names)
except Exception as exc:  # 出力に失敗しても画面の検証結果は表示し続ける
    st.error(f"Excelの作成に失敗しました: {exc}")
else:
    st.download_button(
        "Excelをダウンロード",
        data=excel_bytes,
        file_name=validation_excel_filename(year_month),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="validation_excel_download",
    )

# ---------------------------------------------------------------------------
# 月間一覧
# ---------------------------------------------------------------------------


def _row(day: DailyStaffingResult) -> dict:
    row = {
        "日付": format_date_ja(day.work_date),
        "清掃勤務": day.actual_cleaning_staff,
        "必要": str(day.required_staff) if day.requirement_defined else REQUIREMENT_MISSING_LABEL,
        "上限": "" if day.max_total_staff is None else str(day.max_total_staff),
    }
    for role_id, name in role_names.items():
        row[name] = format_role_cell(day, role_id)
    row["スキル"] = format_skill_cell(day)
    row["未登録"] = day.unmatched_working_count
    row["UNKNOWN"] = day.unknown_cleaning_shift_count
    row["判定"] = STATUS_LABELS[day.status]
    return row


def _filter_days(days: list[DailyStaffingResult], mode: str) -> list[DailyStaffingResult]:
    if mode == FILTER_PROBLEMS:
        return [d for d in days if d.status != VALIDATION_STATUS_OK]
    if mode == FILTER_ERRORS:
        return [d for d in days if d.status == VALIDATION_STATUS_ERROR]
    return list(days)


st.subheader("月間一覧")
st.caption(
    "ロール・スキルは「確定人数 / 必要人数」。不足時の（最大n）は未登録スタッフ・勤務区分不明の人が"
    "条件を満たすと仮定した最大人数です。未登録＝マスター未登録の清掃勤務者数、"
    "UNKNOWN＝清掃所属の勤務区分不明セル数。"
)

mode = st.radio("表示", FILTERS, horizontal=True, key="validation_filter")
shown = _filter_days(result.days, mode)

if shown:
    st.dataframe(pd.DataFrame([_row(d) for d in shown]), width="stretch", hide_index=True)
else:
    st.success("該当する日はありません。")

# ---------------------------------------------------------------------------
# Issue詳細
# ---------------------------------------------------------------------------

problem_days = [d for d in shown if d.issues]
if problem_days:
    st.subheader("問題の詳細")
    for day in problem_days:
        label = f"{format_date_ja(day.work_date)}　{STATUS_LABELS[day.status]}（{len(day.issues)}件）"
        with st.expander(label, expanded=day.status == VALIDATION_STATUS_ERROR):
            for issue in day.issues:
                if issue.severity == VALIDATION_STATUS_ERROR:
                    st.error(issue.message)
                else:
                    st.warning(issue.message)
