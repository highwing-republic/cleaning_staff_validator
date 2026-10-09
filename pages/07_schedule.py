"""勤務表調整画面（手修正・固定・再生成・日別確定）.

ここで扱うのは「現在運用中の勤務表」で、work_date × staff_id につき1件だけ持つ。
10〜14日のローリング運用で期間が重なっても、同じ日の勤務表が二重にならない。

保存するのは計画勤務表で、当日の実績勤務ではない
（当日の変更は印刷した勤務表への手書きで行う運用）。

勤務時刻はPhase 10では直接編集しない。時間を変えたい場合は勤務希望
（早上がり・遅出）を変更して再生成する。
"""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src import services
from src.generation_display import (
    LOCK_MARK,
    MANUAL_MARK,
    format_prefer_off_respect,
    format_schedule_cell,
    format_schedule_change,
    format_schedule_day_status,
    format_shortage_summary,
)
from src.navigation import (
    HEADING_SCHEDULE,
    PAGE_GENERATE,
    PAGE_OUTPUT,
    PAGE_PREFERENCES,
    PAGE_SCHEDULE,
)
from src.period_utils import format_date_short, period_dates
from src.ui_common import open_connection, select_period, show_errors

st.set_page_config(page_title=PAGE_SCHEDULE, layout="wide")
st.title(HEADING_SCHEDULE)
st.caption(
    "保存済みの勤務表を手修正し、変更箇所を固定して再生成できます。"
    f"確定した日は変更されません。勤務時間を変えたい場合は「{PAGE_PREFERENCES}」を直してから再生成してください。"
)

conn = open_connection()

FLASH_KEY = "_schedule_flash"
PREVIEW_KEY = "_schedule_preview"
PREVIEW_CONTEXT_KEY = "_schedule_preview_context"
TOUCHED_KEY = "_schedule_lock_touched"
INPUT_PREFIX = "sched_"
if FLASH_KEY in st.session_state:
    st.success(st.session_state.pop(FLASH_KEY))

start_date, days = select_period("schedule_period")
dates = period_dates(start_date, days)

schedule = services.get_current_schedule(conn, dates)
staff_details = repo.list_staff_details(conn, include_inactive=False)
role_names = services.get_role_names(conn)

# ---------------------------------------------------------------------------
# サマリー
# ---------------------------------------------------------------------------

summary = st.columns(4)
summary[0].metric("作成済み日数", f"{len(schedule.existing_dates)} / {len(dates)}")
summary[1].metric("未作成日数", len(schedule.missing_dates))
summary[2].metric("下書きの日数", len(schedule.draft_dates))
summary[3].metric("確定済みの日数", len(schedule.finalized_dates))
st.caption(f"対象期間: {dates[0]} 〜 {dates[-1]}")

if not schedule.existing_dates:
    st.info(
        "この期間の勤務表はまだ作成されていません。"
        f"「{PAGE_GENERATE}」で勤務案を作成し、下書き保存してください。"
    )
    st.stop()

if schedule.missing_dates:
    st.warning(
        f"勤務表が未作成の日が{len(schedule.missing_dates)}日あります"
        f"（{'、'.join(format_date_short(d) for d in schedule.missing_dates[:5])}"
        f"{' ほか' if len(schedule.missing_dates) > 5 else ''}）。"
        "この画面では自動作成しません。"
    )

# ---------------------------------------------------------------------------
# 勤務表グリッド
# ---------------------------------------------------------------------------

st.subheader("現在の勤務表")
st.caption(f"{LOCK_MARK} は固定、{MANUAL_MARK} は手修正を示します。確定した日は列の状態が「確定」になります。")

status_row = {"スタッフ": "― 状態 ―"}
for view in schedule.days:
    status_row[format_date_short(view.work_date)] = format_schedule_day_status(view)

grid_rows = [status_row]
for detail in staff_details:
    row = {"スタッフ": detail.staff.staff_name}
    for view in schedule.days:
        row[format_date_short(view.work_date)] = format_schedule_cell(
            view.assignments.get(detail.staff.staff_id), view.is_finalized
        )
    grid_rows.append(row)

st.dataframe(pd.DataFrame(grid_rows), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 手修正
# ---------------------------------------------------------------------------

st.subheader("手修正")

editable_dates = schedule.draft_dates
if not editable_dates:
    st.info("この期間は確定済みの日だけです。変更するには確定を解除してください。")
elif not staff_details:
    st.info("有効なスタッフが登録されていません。")
else:
    staff_ids = [d.staff.staff_id for d in staff_details]
    name_by_id = {d.staff.staff_id: d.staff.staff_name for d in staff_details}
    selected_staff_id = st.selectbox(
        "スタッフ",
        options=staff_ids,
        format_func=lambda sid: name_by_id.get(sid, str(sid)),
        key="schedule_staff",
    )

    st.caption(
        "勤務を変えた日は固定が既定で入ります（外すこともできます）。"
        "固定した日は再生成で変更されません。確定済みの日はここに表示されません。"
    )

    # 入力欄のキーにスタッフと期間を含めることで、対象を切り替えたときに
    # 前の入力が残って別の人へ誤保存されることを防ぐ
    context = f"{selected_staff_id}_{start_date}x{days}"
    touched: set[str] = st.session_state.setdefault(TOUCHED_KEY, set())

    def _mark_lock_touched(lock_key: str) -> None:
        """固定を自分で操作した欄は、以降は既定値で上書きしない."""
        touched.add(lock_key)

    header = st.columns([1.2, 1.0, 1.4, 1.0, 1.0])
    header[0].markdown("**日付**")
    header[1].markdown("**状態**")
    header[2].markdown("**現在の勤務**")
    header[3].markdown("**出勤**")
    header[4].markdown("**固定**")

    changes: dict[str, tuple[bool, bool]] = {}
    for view in schedule.days:
        if not view.is_editable:
            continue
        assignment = view.assignments.get(selected_staff_id)
        if assignment is None:
            continue

        prefix = f"{INPUT_PREFIX}{context}_{view.work_date}"
        label_col, status_col, current_col, work_col, lock_col = st.columns(
            [1.2, 1.0, 1.4, 1.0, 1.0]
        )
        with label_col:
            st.markdown(f"**{format_date_short(view.work_date)}**")
        with status_col:
            st.caption(format_schedule_day_status(view))
        with current_col:
            st.caption(format_schedule_cell(assignment))
        with work_col:
            is_working = st.checkbox(
                "出勤",
                value=assignment.is_working,
                key=f"{prefix}_working",
                label_visibility="collapsed",
            )

        lock_key = f"{prefix}_locked"
        if lock_key not in touched:
            # 勤務を変えた日は固定ONを既定値にする（§33）。自分で操作した日は触らない。
            st.session_state[lock_key] = assignment.locked or is_working != assignment.is_working
        with lock_col:
            locked = st.checkbox(
                "固定",
                key=lock_key,
                label_visibility="collapsed",
                on_change=_mark_lock_touched,
                args=(lock_key,),
            )

        if is_working != assignment.is_working or locked != assignment.locked:
            changes[view.work_date] = (is_working, locked)

    if changes:
        st.caption(f"保存する変更: {len(changes)}日")

    if st.button("このスタッフの変更を保存", type="primary"):
        if not changes:
            st.info("変更はありません。")
        else:
            errors = services.save_manual_schedule_changes(conn, selected_staff_id, changes)
            if errors:
                show_errors(errors)
            else:
                st.session_state.pop(PREVIEW_KEY, None)
                # 保存後は固定の既定値を現在値から作り直す
                st.session_state[TOUCHED_KEY] = {
                    k for k in touched if not k.startswith(f"{INPUT_PREFIX}{context}_")
                }
                st.session_state[FLASH_KEY] = (
                    f"{name_by_id[selected_staff_id]} の勤務表を更新しました"
                    f"（{len(changes)}日）。"
                )
                st.rerun()

# ---------------------------------------------------------------------------
# 固定を守って再生成
# ---------------------------------------------------------------------------

st.subheader("固定を守って再生成")

preview_context = (start_date, days)

if st.session_state.get(PREVIEW_CONTEXT_KEY) != preview_context:
    st.session_state.pop(PREVIEW_KEY, None)
    st.session_state[PREVIEW_CONTEXT_KEY] = preview_context

st.caption(
    "固定した勤務と確定した日は変更しません。"
    "ボタンを押しても保存はされず、まず変更内容を確認できます。"
)

if st.button("固定を守って再生成"):
    with st.spinner("再生成しています..."):
        st.session_state[PREVIEW_KEY] = services.preview_regenerated_schedule(conn, dates)

preview = st.session_state.get(PREVIEW_KEY)

if preview is not None:
    if preview.errors:
        show_errors(preview.errors)

    for conflict in preview.conflicts:
        st.error(
            f"{format_date_short(conflict.work_date)} {conflict.staff_name}: {conflict.reason}"
            " 固定を解除するか、勤務希望を修正してください（固定は自動では解除しません）。"
        )

    if preview.result is not None and preview.result.has_solution:
        preview_summary = st.columns(4)
        preview_summary[0].metric("変更されるセル", preview.change_count)
        preview_summary[1].metric("不足の合計", preview.result.total_shortage)
        preview_summary[2].metric("不足のある日", len(preview.result.shortage_days))
        preview_summary[3].metric("希望休の尊重", format_prefer_off_respect(preview.result))
        if preview.skipped_finalized_dates:
            st.caption(
                f"確定済みのため変更しない日: "
                f"{'、'.join(format_date_short(d) for d in preview.skipped_finalized_dates)}"
            )

        for day in preview.result.shortage_days:
            st.warning(
                f"{format_date_short(day.work_date)}: "
                f"{format_shortage_summary(day, role_names)}"
            )

        if preview.changes:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "スタッフ": change.staff_name,
                            "日付": format_date_short(change.work_date),
                            "変更": format_schedule_change(change),
                        }
                        for change in preview.changes
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("変更されるセルはありません。")

        if st.button("再生成結果を反映", type="primary", disabled=not preview.can_apply):
            run_id, apply_errors = services.apply_regenerated_schedule(conn, dates, preview)
            if apply_errors:
                show_errors(apply_errors)
            else:
                st.session_state.pop(PREVIEW_KEY, None)
                st.session_state[FLASH_KEY] = (
                    f"再生成結果を反映しました（変更 {preview.change_count} 件）。"
                )
                st.rerun()
    elif preview.result is not None:
        st.error(
            "再生成できませんでした。固定した条件が現在の勤務条件と矛盾しています。"
            f"固定を解除するか、「{PAGE_PREFERENCES}」で勤務希望を確認してください。"
        )
        with st.expander("詳しい情報"):
            st.caption(f"計算結果: {preview.result.solver_status}")

# ---------------------------------------------------------------------------
# 日別の確定
# ---------------------------------------------------------------------------

st.subheader("日別の確定")
st.caption(
    "確定した日は手修正・再生成の対象外になります。"
    "確定を解除すると再び変更できます。"
)

status_table = pd.DataFrame(
    [
        {
            "日付": format_date_short(view.work_date),
            "状態": format_schedule_day_status(view),
            "出勤人数": view.working_count if view.exists else 0,
            "固定": view.locked_count if view.exists else 0,
        }
        for view in schedule.days
    ]
)
st.dataframe(status_table, width="stretch", hide_index=True)

existing = schedule.existing_dates
if existing:
    target_date = st.selectbox(
        "対象日",
        options=existing,
        format_func=lambda d: f"{format_date_short(d)}（{format_schedule_day_status(schedule.day(d))}）",
        key="schedule_finalize_date",
    )
    target_view = schedule.day(target_date)

    if target_view is not None and target_view.is_finalized:
        if st.button("確定を解除"):
            errors = services.unfinalize_schedule_day(conn, target_date)
            if errors:
                show_errors(errors)
            else:
                st.session_state.pop(PREVIEW_KEY, None)
                st.session_state[FLASH_KEY] = (
                    f"{format_date_short(target_date)} の確定を解除しました。"
                )
                st.rerun()
    else:
        if st.button("この日を確定", type="primary"):
            errors = services.finalize_schedule_day(conn, target_date)
            if errors:
                show_errors(errors)
            else:
                st.session_state.pop(PREVIEW_KEY, None)
                st.session_state[FLASH_KEY] = (
                    f"{format_date_short(target_date)} を確定しました。"
                )
                st.rerun()

st.divider()
st.caption(
    f"手直しと確定が終わったら、「{PAGE_OUTPUT}」でExcelを出力して印刷してください。"
)
