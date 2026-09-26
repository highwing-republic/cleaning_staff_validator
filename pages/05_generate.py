"""シフト生成画面（T72〜T74）."""

import streamlit as st

from src import repositories as repo
from src import services
from src.constants import STAGES, STAGE_LABELS
from src.month_utils import get_month_dates
from src.ui_common import confirmed_banner, open_connection, select_year_month, show_errors

st.set_page_config(page_title="シフト生成", layout="wide")
st.title("⑤⑥ 事前チェック・シフト生成")

conn = open_connection()

year_month = select_year_month()

is_confirmed = confirmed_banner(conn, year_month)

# ---------------------------------------------------------------------------
# 入力状況
# ---------------------------------------------------------------------------

st.subheader("入力状況")

active_staff = repo.list_staff(conn, include_inactive=False)
conditions = repo.get_monthly_conditions(conn, year_month)
requirements = repo.get_daily_requirements(conn, year_month)

total_days = len(get_month_dates(year_month))

col1, col2, col3 = st.columns(3)
col1.metric("有効スタッフ数", len(active_staff))
col2.metric("月間勤務条件 入力済み", f"{len(conditions)} / {len(active_staff)}")
col3.metric("日別最低人数 入力済み", f"{len(requirements)} / {total_days}")

existing_month = repo.get_schedule_month(conn, year_month)
if existing_month is not None:
    st.warning("固定していない手動変更は再計算で変更されます。")

# ---------------------------------------------------------------------------
# 事前チェック
# ---------------------------------------------------------------------------

st.subheader("事前チェック")

if st.button("事前チェックを実行", disabled=is_confirmed):
    result = services.run_precheck_for_month(conn, year_month)
    if result.is_ok:
        st.success("事前チェックOK。シフト生成に進めます。")
    else:
        st.error(f"{len(result.errors)}件の問題があります。")
        show_errors(result.errors)

# ---------------------------------------------------------------------------
# シフト生成
# ---------------------------------------------------------------------------

st.subheader("シフト生成")

STATUS_LABELS = {
    "OPTIMAL": "最適解",
    "FEASIBLE": "有効解（最適性未確認）",
    "INFEASIBLE": "作成不可",
    "UNKNOWN": "時間内に解が見つからず",
}

if st.button("シフト生成を実行", type="primary", disabled=is_confirmed):
    try:
        outcome = services.generate_and_save(conn, year_month)
    except services.ScheduleConfirmedError as exc:
        st.error(str(exc))
    else:
        if not outcome.precheck.is_ok:
            st.error("事前チェックでエラーがあるため、シフト生成を中止しました。")
            show_errors(outcome.precheck.errors)
        else:
            result = outcome.result
            status_label = STATUS_LABELS.get(result.status, result.status)
            st.write(f"### Solver結果: {status_label}")

            if result.status == "INFEASIBLE":
                st.error("現在の条件ではシフトを作成できません。")
                st.markdown(
                    "以下を確認してください。\n\n"
                    "- 最低人数\n"
                    "- 必要ロール\n"
                    "- 絶対休み\n"
                    "- 通常勤務可能曜日\n"
                    "- 最大勤務時間\n"
                    "- 最低勤務時間\n"
                    "- 最大連続勤務\n"
                    "- 固定条件"
                )
            elif result.status == "UNKNOWN":
                st.warning("時間内に解を見つけられませんでした。既存のシフトデータは変更していません。")
            else:
                st.success("シフトを保存しました。" if outcome.saved else "シフトを生成しました。")
                st.caption(f"完了ステージ: {result.completed_stage}/5")

                def _fmt(value):
                    return "—" if value is None else value

                stage_values = {
                    1: result.objective_target_deviation,
                    2: result.objective_max_deviation,
                    3: result.objective_max_overstaff,
                    4: result.objective_prefer_off,
                    5: result.objective_prefer_work,
                }
                obj_cols = st.columns(len(STAGES) + 1)
                for col, stage in zip(obj_cols, STAGES):
                    col.metric(f"{stage}. {STAGE_LABELS[stage]}", _fmt(stage_values[stage]))
                obj_cols[-1].metric("最低人数を超える出勤（合計）", _fmt(result.objective_overstaff))

                st.info("結果は「⑦管理者確認」（06_schedule）で確認・編集できます。")
