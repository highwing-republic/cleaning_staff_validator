"""シフト生成画面（OR-Tools CP-SATによる勤務案の自動作成）.

スタッフマスターの通常条件、期間別勤務希望、日別の必要人員から
出勤/休みを決定する。勤務時刻はSolverが決めず、勤務希望を反映した
実効勤務時間をそのまま使う。

人員が足りない日も「作成できません」とせず、配置できた分と不足を返す
（現場では人員不足が構造的に起こるため、不足の可視化そのものが成果になる）。

最低必要人数を満たしたうえで、希望休を出していないスタッフを優先し、
各スタッフの目標勤務日数へ近づける。目標と公平性のために最低必要人数を
上回る配置も許容する。希望休の日に勤務したことや
目標勤務日数からの差は制約違反ではないため、警告ではなくスタッフ別の一覧で示す。
作成した勤務案は「下書き保存」でDBへ保存し、以降の手修正・固定・再生成・確定は
「勤務表調整」で行う。すでに勤務表がある日を含む期間はここから上書きしない
（確定済みの日や固定した勤務を取り違えないため、調整画面からの再生成へ案内する）。
"""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src import services
from src.generation_display import (
    REQUIREMENT_MISSING_LABEL,
    format_assignment_cell,
    format_prefer_off_respect,
    format_required,
    format_role_shortages,
    format_scheduled_days,
    format_shortage_summary,
    format_skill_shortage,
    format_staff_shortage,
    format_status,
    format_target_days,
)
from src.navigation import (
    HEADING_GENERATE,
    PAGE_GENERATE,
    PAGE_PREFERENCES,
    PAGE_REQUIREMENTS,
    PAGE_SCHEDULE,
    PAGE_STAFF,
)
from src.period_utils import format_date_short, period_dates
from src.requirement_display import count_defined, count_undefined
from src.ui_common import open_connection, select_period, show_errors

st.set_page_config(page_title=PAGE_GENERATE, layout="wide")
st.title(HEADING_GENERATE)
st.caption(
    "通常勤務条件・勤務希望・必要人員から勤務案を作成します。"
    "人員が足りない日も案を作成し、不足として表示します。"
    "この画面では結果を保存しません。"
)

conn = open_connection()

# 保存後は st.rerun() で画面を作り直すため、完了メッセージはsession_state経由で持ち越す
FLASH_KEY = "_generation_flash"
if FLASH_KEY in st.session_state:
    st.success(st.session_state.pop(FLASH_KEY))

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
st.caption("期間の前後にある勤務表を連勤の判定に使っています。")

if not staff_details:
    st.info(
        f"スタッフがまだ登録されていません。最初に「{PAGE_STAFF}」から登録してください。"
    )
    st.stop()

undefined_dates = [v.work_date for v in requirement_views if not v.is_defined]
if undefined_dates:
    shown = "、".join(format_date_short(d) for d in undefined_dates[:5])
    more = f" ほか{len(undefined_dates) - 5}日" if len(undefined_dates) > 5 else ""
    st.warning(
        f"必要人数が未入力の日があります（{shown}{more}）。"
        "勤務案は作成できますが、その日は必要人数を基準に配置できません。"
        f"「{PAGE_REQUIREMENTS}」で入力してください。"
    )

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
        "勤務希望と必要人数が両立しない可能性があります。"
        f"「{PAGE_PREFERENCES}」の絶対休みと「{PAGE_REQUIREMENTS}」の必要人数を確認してください。"
    )
    with st.expander("詳しい情報"):
        st.caption(f"計算結果: {result.solver_status} / 計算時間 {result.solve_seconds:.2f} 秒")
    st.stop()

# ---------------------------------------------------------------------------
# 結果サマリー
# ---------------------------------------------------------------------------

st.subheader("作成結果")
result_summary = st.columns(4)
result_summary[0].metric("総出勤日数", f"{result.total_workdays}日")
result_summary[1].metric("不足の合計", result.total_shortage)
result_summary[2].metric("不足のある日", len(result.shortage_days))
result_summary[3].metric("希望休の尊重", format_prefer_off_respect(result))
with st.expander("詳しい情報"):
    # 内部の計算状態は普段見る必要がないため、ここに畳んでおく
    st.caption(f"計算時間 {result.solve_seconds:.2f} 秒 / 計算結果: {result.solver_status}")

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
# スタッフ別の勤務状況
# ---------------------------------------------------------------------------

st.subheader("スタッフ別の勤務状況")
st.caption(
    "「目安」は目標勤務日数/週を対象期間に換算した日数です。"
    "未設定の場合は、チェッカーを週5日、その他を最低必要人数の平均から自動計算します。"
    "必要人数を満たすために目安から離れることや、希望休の日に勤務することはあります"
    "（いずれも条件違反ではありません）。"
)

staff_table = pd.DataFrame(
    [
        {
            "スタッフ": summary.staff_name,
            "実勤務": format_scheduled_days(summary),
            "目安": format_target_days(summary),
            "希望休": summary.prefer_off_requested_count,
            "希望休出勤": summary.prefer_off_worked_count,
        }
        for summary in result.staff_summaries
    ]
)
st.dataframe(staff_table, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 日別サマリー
# ---------------------------------------------------------------------------

st.subheader("日別の充足状況")

daily = pd.DataFrame(
    [
        {
            "日付": format_date_short(day.work_date),
            "状態": format_status(day),
            "最低必要": format_required(day),
            "配置": day.scheduled_staff_count,
            "人数不足": format_staff_shortage(day),
            "ロール不足": format_role_shortages(day, role_names),
            "スキル不足": format_skill_shortage(day),
        }
        for day in result.days
    ]
)
st.dataframe(daily, width="stretch", hide_index=True)
# ---------------------------------------------------------------------------
# 下書き保存
# ---------------------------------------------------------------------------

st.subheader("保存")

existing_dates = services.get_current_schedule(conn, dates).existing_dates
if existing_dates:
    st.info(
        f"この期間にはすでに勤務表がある日が{len(existing_dates)}日あります"
        f"（{format_date_short(existing_dates[0])}〜{format_date_short(existing_dates[-1])}）。"
        "確定した日や固定した勤務を消さないよう、ここからは保存しません。"
        f"すでにある日を変更する場合は「{PAGE_SCHEDULE}」から、"
        f"続きを作る場合は開始日を {format_date_short(existing_dates[-1])} の翌日以降にしてください。"
    )
else:
    st.caption(
        f"下書き保存すると、以降は「{PAGE_SCHEDULE}」で手修正・固定・再生成・確定ができます。"
        "保存しない場合、この勤務案は期間を変えると消えます。"
    )
    if st.button("勤務案を下書き保存", type="primary"):
        run_id, save_errors = services.save_generated_schedule(conn, dates, result)
        if save_errors:
            show_errors(save_errors)
        else:
            st.session_state[FLASH_KEY] = (
                f"勤務案を下書き保存しました（{dates[0]} 〜 {dates[-1]}）。"
                f"「{PAGE_SCHEDULE}」で変更できます。"
            )
            st.rerun()

st.divider()
st.caption(f"勤務案を下書き保存したら、「{PAGE_SCHEDULE}」で手直しして日ごとに確定してください。")
