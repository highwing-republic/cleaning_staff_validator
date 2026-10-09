"""勤務表出力画面（最終確認・検証・Excel出力）.

ここは編集画面ではない。勤務を変えるときは「⑦ 勤務表調整」で直す。
検証対象は保存済みの勤務表（計画）で、勤怠CSVの実績とは別物。
押した時点の勤務表をそのままExcelへ出すため、手修正・再生成の直後でも最新値になる。
"""

import pandas as pd
import streamlit as st

from src import services
from src.constants import (
    VALIDATION_STATUS_ERROR,
    VALIDATION_STATUS_INFO,
    VALIDATION_STATUS_WARNING,
)
from src.models import ScheduleDayValidationResult
from src.period_utils import format_date_short, period_dates
from src.schedule_excel import schedule_excel_filename
from src.schedule_output_display import (
    DRAFT_NOTICE,
    SEVERITY_MARKS,
    format_day_state,
    format_required,
    format_reserved_rooms,
    format_role,
    format_schedule_status,
    format_scheduled,
    format_skill,
    format_staff_count,
    severity_sort_key,
)
from src.ui_common import open_connection, select_period

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

FILTER_PROBLEMS = "問題のある日だけ"
FILTER_ALL = "全日"
FILTERS = (FILTER_PROBLEMS, FILTER_ALL)

st.set_page_config(page_title="勤務表出力", layout="wide")
st.title("⑧ 勤務表出力")
st.caption(
    "保存済みの勤務表を最終チェックし、印刷用のExcelを出力します。"
    "勤務を変えるときは「⑦ 勤務表調整」で直してから、この画面に戻ってください。"
)

conn = open_connection()

start_date, days = select_period("output_period")
dates = period_dates(start_date, days)

# 毎回DBから読み直す（手修正・再生成の直後でも古い結果を表示しない）
validation = services.validate_current_schedule(conn, dates)
role_names = services.get_role_names(conn)

# ---------------------------------------------------------------------------
# サマリー
# ---------------------------------------------------------------------------

top = st.columns(4)
top[0].metric("作成済み日数", f"{validation.existing_days} / {len(dates)}")
top[1].metric("未作成日数", validation.missing_days)
top[2].metric("確定日数", validation.finalized_days)
top[3].metric("下書き日数", validation.draft_days)

bottom = st.columns(4)
bottom[0].metric("問題なし日数", validation.clear_days)
bottom[1].metric("不足などの問題がある日数", validation.problem_days)
bottom[2].metric("要件未設定日数", validation.requirement_missing_days)
bottom[3].metric("不足人数の合計", validation.total_shortage)
st.caption(f"対象期間: {dates[0]} 〜 {dates[-1]}")

if validation.existing_days == 0:
    st.info(
        "この期間の勤務表はまだ作成されていません。"
        "「⑥ シフト生成」で勤務案を作成し、下書き保存してください。"
    )

if validation.missing_days:
    st.warning(
        f"勤務表が未作成の日が{validation.missing_days}日あります。"
        "Excelには「未作成」と表示されます。"
    )
if validation.has_draft:
    st.info(f"{DRAFT_NOTICE}（Excelの勤務表にも明記されます）。")
if validation.problem_days:
    st.warning(
        f"不足などの問題がある日が{validation.problem_days}日あります。"
        "この状態でもExcelは出力できます。"
    )

# ---------------------------------------------------------------------------
# Excel出力
# ---------------------------------------------------------------------------

st.subheader("Excel出力")
st.caption(
    "「勤務表」「日別確認」「問題一覧」「出力情報」の4シートです。"
    "勤務表シートはA4横・横1ページに収まる設定で、当日の変更を手書きできる備考欄が付いています。"
)

try:
    excel_bytes = services.export_schedule_excel(conn, dates)
except Exception as exc:  # 出力に失敗しても検証結果は見られるようにする
    st.error(f"Excelの作成に失敗しました: {exc}")
else:
    st.download_button(
        "勤務表Excelをダウンロード",
        data=excel_bytes,
        file_name=schedule_excel_filename(dates),
        mime=EXCEL_MIME,
        type="primary",
        key="schedule_excel_download",
    )

# ---------------------------------------------------------------------------
# 日別検証
# ---------------------------------------------------------------------------

st.subheader("日別の確認")
st.caption(
    "ロール・スキルは「配置人数 / 必要人数」。"
    "「未作成」の日は勤務表そのものがないため、人数の判定を行いません。"
)


def _row(day: ScheduleDayValidationResult) -> dict:
    row = {
        "日付": format_date_short(day.work_date),
        "状態": format_schedule_status(day),
        "予約": format_reserved_rooms(day),
        "必要": format_required(day),
        "配置": format_scheduled(day),
        "人数": format_staff_count(day),
    }
    for role_id, name in role_names.items():
        row[name] = format_role(day, role_id)
    row["スキル"] = format_skill(day)
    row["判定"] = SEVERITY_MARKS.get(day.status, day.status)
    return row


st.dataframe(
    pd.DataFrame([_row(d) for d in validation.days]), width="stretch", hide_index=True
)

# ---------------------------------------------------------------------------
# 問題一覧
# ---------------------------------------------------------------------------

st.subheader("問題一覧")

mode = st.radio("表示", FILTERS, horizontal=True, key="output_issue_filter")
issues = sorted(validation.issues, key=severity_sort_key)
if mode == FILTER_PROBLEMS:
    issues = [i for i in issues if i.severity != VALIDATION_STATUS_INFO]

if issues:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "日付": format_date_short(issue.work_date),
                    "判定": SEVERITY_MARKS.get(issue.severity, issue.severity),
                    "種類": issue.code,
                    "内容": issue.message,
                }
                for issue in issues
            ]
        ),
        width="stretch",
        hide_index=True,
    )
else:
    st.success("問題は見つかりませんでした。")

problem_days = [
    d
    for d in validation.days
    if any(i.severity in (VALIDATION_STATUS_ERROR, VALIDATION_STATUS_WARNING) for i in d.issues)
]
if problem_days:
    st.caption("日ごとの詳細を開くと、その日の問題と直し方の目安が分かります。")
    for day in problem_days:
        label = (
            f"{format_date_short(day.work_date)}　{format_day_state(day)}"
            f"（{len(day.issues)}件）"
        )
        with st.expander(label, expanded=day.status == VALIDATION_STATUS_ERROR):
            for issue in day.issues:
                if issue.severity == VALIDATION_STATUS_ERROR:
                    st.error(issue.message)
                elif issue.severity == VALIDATION_STATUS_WARNING:
                    st.warning(issue.message)
                else:
                    st.info(issue.message)
