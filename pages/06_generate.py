"""シフト生成画面（OR-Tools CP-SATによる勤務案の自動作成）.

スタッフマスターの通常条件、期間別勤務希望、日別の必要人員から
出勤/休みを決定する。勤務時刻はSolverが決めず、勤務希望を反映した
実効勤務時間をそのまま使う。

人員が足りない日も「作成できません」とせず、配置できた分と不足を返す
（現場では人員不足が構造的に起こるため、不足の可視化そのものが成果になる）。
Phase 8では生成結果をDBへ保存しない（画面表示のみ）。
"""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src import services
from src.generation_display import (
    REQUIREMENT_MISSING_LABEL,
    format_assignment_cell,
    format_required,
    format_role_shortages,
    format_shortage_summary,
    format_skill_shortage,
    format_staff_shortage,
    format_status,
)
from src.period_utils import format_date_short, period_dates
from src.requirement_display import count_defined, count_undefined
from src.ui_common import open_connection, select_period

st.set_page_config(page_title="シフト生成", layout="wide")
st.title("⑥ シフト生成")
st.caption(
    "通常勤務条件・勤務希望・必要人員から勤務案を作成します。"
    "人員が足りない日も案を作成し、不足として表示します。"
    "この画面では結果を保存しません。"
)

conn = open_connection()

start_date, days = select_period("generation_period")
dates = period_dates(start_date, days)

staff_details = repo.list_staff_details(conn, include_inactive=False)
requirement_views = services.get_period_requirements(conn, dates)
role_names = services.get_role_names(conn)

# ---------------------------------------------------------------------------
# 生成前サマリー
# ---------------------------------------------------------------------------

st.subheader("生成前の確認")
summary = st.columns(4)
summary[0].metric("対象期間", f"{len(dates)}日間")
summary[1].metric("有効スタッフ数", f"{len(staff_details)}名")
summary[2].metric("要件設定済み日数", count_defined(requirement_views))
summary[3].metric("要件未設定日数", count_undefined(requirement_views))
st.caption(
    f"対象期間: {dates[0]} 〜 {dates[-1]}。"
    f"{REQUIREMENT_MISSING_LABEL}の日は人数・ロール・スキルの条件を設定しないため、"
    "0名配置になることがあります。"
)

if not staff_details:
    st.info("有効なスタッフが登録されていません。先に「① スタッフ管理」で登録してください。")
    st.stop()

# ---------------------------------------------------------------------------
# 入力警告（生成前に確認できるようにする）
# ---------------------------------------------------------------------------

request_preview = services.build_generation_request(conn, dates)
excluded = [
    (staff.staff_name, work_date)
    for staff in request_preview.staff
    for work_date, condition in sorted(staff.day_conditions.items())
    if condition.can_work and condition.time_status != "OK"
]
if excluded:
    with st.expander(f"入力の注意: 候補から除外される日があります（{len(excluded)}件）", expanded=True):
        st.caption(
            "通常勤務時間が未設定、または勤務時間が成立しないため、"
            "その日の候補から除外します（勝手な時刻では勤務させません）。"
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {"スタッフ": name, "日付": format_date_short(work_date)}
                    for name, work_date in excluded
                ]
            ),
            width="stretch",
            hide_index=True,
        )

# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------

RESULT_KEY = "_generation_result"
CONTEXT_KEY = "_generation_context"
context = (start_date, days)

if st.session_state.get(CONTEXT_KEY) != context:
    # 期間を変えたら前回の結果を残さない（別期間の結果を見せないため）
    st.session_state.pop(RESULT_KEY, None)
    st.session_state[CONTEXT_KEY] = context

if st.button("勤務案を作成", type="primary"):
    with st.spinner("勤務案を作成しています..."):
        st.session_state[RESULT_KEY] = services.generate_schedule(conn, dates)

result = st.session_state.get(RESULT_KEY)
if result is None:
    st.info("「勤務案を作成」を押すと勤務案を作成します。")
    st.stop()

if not result.has_solution:
    st.error(
        "勤務案を作成できませんでした。"
        f"（計算結果: {result.solver_status}）条件を見直してください。"
    )
    st.stop()

# ---------------------------------------------------------------------------
# 結果サマリー
# ---------------------------------------------------------------------------

st.subheader("作成結果")
result_summary = st.columns(3)
result_summary[0].metric("総出勤日数", f"{result.total_workdays}日")
result_summary[1].metric("不足の合計", result.total_shortage)
result_summary[2].metric("不足のある日", len(result.shortage_days))
st.caption(f"計算時間 {result.solve_seconds:.2f} 秒（{result.solver_status}）")

for day in result.shortage_days:
    st.warning(
        f"{format_date_short(day.work_date)}: "
        f"{format_shortage_summary(day, role_names)}"
    )

# ---------------------------------------------------------------------------
# 生成結果グリッド（スタッフ × 日付）
# ---------------------------------------------------------------------------

st.subheader("勤務案")

cells: dict[int, dict[str, str]] = {}
for assignment in result.assignments:
    cells.setdefault(assignment.staff_id, {})[assignment.work_date] = format_assignment_cell(
        assignment.start_time, assignment.end_time, assignment.is_working
    )

grid = pd.DataFrame(
    [
        {
            "スタッフ": detail.staff.staff_name,
            "出勤日数": sum(
                1 for a in result.working_assignments(detail.staff.staff_id)
            ),
            **{
                format_date_short(work_date): cells.get(detail.staff.staff_id, {}).get(
                    work_date, ""
                )
                for work_date in dates
            },
        }
        for detail in staff_details
    ]
)
st.dataframe(grid, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 日別サマリー
# ---------------------------------------------------------------------------

st.subheader("日別の充足状況")

daily = pd.DataFrame(
    [
        {
            "日付": format_date_short(day.work_date),
            "状態": format_status(day),
            "必要": format_required(day),
            "配置": day.scheduled_staff_count,
            "人数不足": format_staff_shortage(day),
            "ロール不足": format_role_shortages(day, role_names),
            "スキル不足": format_skill_shortage(day),
        }
        for day in result.days
    ]
)
st.dataframe(daily, width="stretch", hide_index=True)
st.caption(
    "この勤務案は保存されません（期間を変えると消えます）。"
    "手修正・固定して再作成は後続のPhaseで追加します。"
)
