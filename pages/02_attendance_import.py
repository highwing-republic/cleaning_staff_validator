"""勤怠CSV取込画面.

CSVを選択するとプレビューのみ行い、DBには保存しない。
「この内容を有効版として取り込む」を押したときだけ保存する（2段階）。
"""

import pandas as pd
import streamlit as st

from src import attendance_import as parser
from src import repositories as repo
from src import services
from src.models import AttendancePreview, ImportIssue
from src.navigation import (
    HEADING_ATTENDANCE_IMPORT,
    PAGE_ATTENDANCE_IMPORT,
)
from src.ui_common import format_year_month_ja, open_connection

IMPORT_BUTTON_LABEL = "この内容を有効版として取り込む"

# warning の表示区分（表示順）
WARNING_SECTIONS = [
    (services.UNMATCHED_STAFF, "未登録スタッフ"),
    (services.NAME_MISMATCH, "氏名不一致"),
    (services.DEPARTMENT_MISMATCH, "部門不一致"),
    (services.INACTIVE_STAFF, "無効スタッフ"),
    (parser.UNKNOWN_SHIFT, "UNKNOWN勤務区分"),
    (parser.EMPLOYEE_NAME_BLANK, "氏名空欄"),
    (parser.DEPARTMENT_BLANK, "部門空欄"),
]

st.set_page_config(page_title=PAGE_ATTENDANCE_IMPORT, layout="wide")
st.title(HEADING_ATTENDANCE_IMPORT)
st.caption(
    "勤怠システムから出力した月間シフトCSV（従業員番号・freee人事労務での表示名・部門・日付列）を取り込みます。"
    "ファイルを選択しただけでは保存されません。"
)

conn = open_connection()

if "attendance_upload_nonce" not in st.session_state:
    st.session_state["attendance_upload_nonce"] = 0

completed = st.session_state.pop("attendance_import_completed", None)
if completed:
    st.success(completed)


def _issue_table(issues: list[ImportIssue]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "行": i.row_number,
                "従業員番号": i.employee_code or "",
                "氏名(CSV)": i.employee_name or "",
                "日付": i.work_date or "",
                "内容": i.message,
            }
            for i in issues
        ]
    )


def _show_summary(preview: AttendancePreview) -> None:
    st.markdown(f"**ファイル名**: {preview.source_filename}")
    cols = st.columns(7)
    cols[0].metric("対象月", format_year_month_ja(preview.year_month) if preview.year_month else "-")
    cols[1].metric("従業員数", preview.employee_count)
    cols[2].metric("入力済みシフトセル数", preview.shift_count)
    cols[3].metric("清掃所属人数", preview.cleaning_employee_count)
    cols[4].metric("未登録スタッフ数", preview.unmatched_count)
    cols[5].metric("UNKNOWN勤務セル数", preview.unknown_shift_count)
    cols[6].metric("警告件数", len(preview.warnings))
    st.caption("入力済みシフトセル数 = 空欄以外のセル数（別業務・UNKNOWNを含みます。勤務人数ではありません）。")


def _show_warnings(preview: AttendancePreview) -> None:
    if not preview.warnings:
        st.success("警告はありません。")
        return
    st.warning(f"警告が{len(preview.warnings)}件あります（取込は可能です）。内容を確認してください。")
    for code, label in WARNING_SECTIONS:
        issues = [w for w in preview.warnings if w.code == code]
        if not issues:
            continue
        with st.expander(f"{label}（{len(issues)}件）", expanded=code == services.UNMATCHED_STAFF):
            st.dataframe(_issue_table(issues), width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# 現在の有効版
# ---------------------------------------------------------------------------

st.subheader("現在の有効版")
active_imports = repo.list_active_imports(conn)
if active_imports:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "対象月": format_year_month_ja(a.year_month),
                    "ファイル名": a.source_filename,
                    "取込日時": a.imported_at,
                    "従業員数": a.employee_count,
                    "未登録人数": a.unmatched_count,
                }
                for a in active_imports
            ]
        ),
        width="stretch",
        hide_index=True,
    )
else:
    st.info("有効な取込はまだありません。")

# ---------------------------------------------------------------------------
# アップロード → プレビュー → 取込
# ---------------------------------------------------------------------------

st.subheader("CSVを選択")
uploaded = st.file_uploader(
    "勤怠CSVファイル",
    type=["csv"],
    key=f"attendance_upload_{st.session_state['attendance_upload_nonce']}",
)

if uploaded is None:
    st.caption("CSVファイルを選択すると、保存前に内容を確認できます。")
    st.stop()

preview = services.preview_attendance_csv(conn, uploaded.getvalue(), uploaded.name)

st.subheader("取込前プレビュー")

if preview.errors:
    st.error(f"エラーが{len(preview.errors)}件あるため取り込めません。CSVを修正して選択し直してください。")
    st.dataframe(_issue_table(preview.errors), width="stretch", hide_index=True)

if preview.year_month is not None:
    _show_summary(preview)
    _show_warnings(preview)

    existing = repo.get_active_import(conn, preview.year_month)
    if existing is not None and not preview.errors:
        st.warning(
            f"{format_year_month_ja(preview.year_month)}には既に有効な取込があります"
            f"（{existing.source_filename}、{existing.imported_at}）。"
            "新しいCSVを取り込むと、現在の取込は履歴として残り、新しいCSVが有効版になります。"
        )

if st.button(IMPORT_BUTTON_LABEL, type="primary", disabled=not preview.can_import):
    import_id = services.import_attendance(conn, preview)
    st.session_state["attendance_import_completed"] = (
        f"取込が完了しました。{format_year_month_ja(preview.year_month)}の有効版を更新しました"
        f"（{preview.source_filename}、取込ID {import_id}）。"
    )
    # アップロード欄をリセットし、同じ内容の二重取込を防ぐ
    st.session_state["attendance_upload_nonce"] += 1
    st.rerun()
