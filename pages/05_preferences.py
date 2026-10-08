"""期間別勤務希望入力画面.

スタッフ本人はアプリを操作しない。紙で提出された希望を若女将が転記する前提のため、
1スタッフ分の対象期間（最大14日）をページ遷移なしでまとめて入力・保存できるようにする。

スタッフマスター = 普段どう働く人か / この画面 = 今回だけ何が違うか。
通常どおりの日はレコードを作らない（行がない = 通常）。
"""

import pandas as pd
import streamlit as st

from src import repositories as repo
from src import services
from src.constants import TIME_STATUS_UNSET
from src.models import StaffDatePreferenceInput
from src.period_utils import period_dates
from src.preference_display import (
    NOTE_MARK,
    format_base_availability,
    format_date_short,
    format_day_condition,
    format_effective_time,
)
from src.ui_common import open_connection, select_period, show_errors
from src.work_time import format_standard_work_time

st.set_page_config(page_title="勤務希望入力", layout="wide")
st.title("⑤ 勤務希望入力")
st.caption(
    "「普段と違う希望だけ」を入力します。変更がない日は入力不要で「通常」と表示されます。"
    "早上がり・遅出は「その日に出勤するなら何時か」であり、出勤を強制しません。"
)

conn = open_connection()

# 保存後は st.rerun() で一覧を作り直すため、完了メッセージはsession_state経由で持ち越す
FLASH_KEY = "_preference_flash"
if FLASH_KEY in st.session_state:
    st.success(st.session_state.pop(FLASH_KEY))

start_date, days = select_period()
dates = period_dates(start_date, days)

staff_details = repo.list_staff_details(conn, include_inactive=False)
if not staff_details:
    st.info("有効なスタッフが登録されていません。先に「① スタッフ管理」で登録してください。")
    st.stop()

# ---------------------------------------------------------------------------
# スタッフ × 日付 の一覧
# ---------------------------------------------------------------------------

st.subheader("期間内の勤務希望")
st.caption(f"{NOTE_MARK} は備考ありを示します。絶休=絶対休み / 希休=できれば休み / 勤務可=通常休みだが勤務可能。")

conditions_by_staff = services.get_period_conditions_by_staff(conn, dates)

overview_rows = []
for detail in staff_details:
    conditions = conditions_by_staff.get(detail.staff.staff_id, [])
    row = {"スタッフ": detail.staff.staff_name}
    for condition in conditions:
        row[format_date_short(condition.work_date)] = format_day_condition(condition)
    overview_rows.append(row)

st.dataframe(pd.DataFrame(overview_rows), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 1スタッフ分の一括編集
# ---------------------------------------------------------------------------

st.subheader("希望の入力")

staff_ids = [d.staff.staff_id for d in staff_details]
name_by_id = {d.staff.staff_id: d.staff.staff_name for d in staff_details}
selected_staff_id = st.selectbox(
    "スタッフ",
    options=staff_ids,
    format_func=lambda sid: name_by_id.get(sid, str(sid)),
    key="preference_staff",
)
detail = repo.get_staff_detail(conn, selected_staff_id)

standard_time = format_standard_work_time(
    detail.staff.standard_start_time, detail.staff.standard_end_time
)
st.caption(
    f"通常 {standard_time}" if standard_time
    else "通常勤務時間が未設定です（早上がり・遅出から実効勤務時間を確定できません）。"
)

saved = {
    p.work_date: p
    for p in repo.list_staff_preferences(conn, selected_staff_id, dates[0], dates[-1])
}
conditions = {c.work_date: c for c in services.get_period_conditions(conn, detail, dates)}


def _reset_inputs_on_context_change() -> None:
    """対象スタッフ・期間が変わったら前の入力内容を残さない.

    未保存の値が別スタッフ・別期間の画面に残ると誤って保存されるため。
    """
    context = (selected_staff_id, start_date, days)
    if st.session_state.get("_preference_context") == context:
        return
    st.session_state["_preference_context"] = context
    for key in [k for k in st.session_state if k.startswith("pref_")]:
        del st.session_state[key]


_reset_inputs_on_context_change()


def _day_inputs(work_date: str) -> StaffDatePreferenceInput:
    """1日分の入力欄. 既存の希望を初期値にする."""
    pref = saved.get(work_date)
    condition = conditions[work_date]
    prefix = f"pref_{work_date}"

    label, base, effective, extra, note_col = st.columns([1.3, 0.9, 1.4, 2.6, 2.0])
    with label:
        st.markdown(f"**{format_date_short(work_date)}**")
    with base:
        st.caption(format_base_availability(condition))
    with effective:
        st.caption(format_effective_time(condition))

    with extra:
        off_col, prefer_col, extra_col = st.columns(3)
        with off_col:
            absolute_off = st.checkbox(
                "絶対休み",
                value=bool(pref and pref.absolute_off),
                key=f"{prefix}_absolute_off",
            )
        with prefer_col:
            prefer_off = st.checkbox(
                "できれば休み",
                value=bool(pref and pref.prefer_off),
                key=f"{prefix}_prefer_off",
            )
        with extra_col:
            available_extra = st.checkbox(
                "通常外だが勤務可能",
                value=bool(pref and pref.available_extra),
                key=f"{prefix}_available_extra",
                disabled=condition.base_available,
                help=(
                    "通常勤務曜日のため指定不要です。"
                    if condition.base_available
                    else "通常は勤務しない曜日だが、今回は勤務できる場合にチェックします。"
                ),
            )

    with note_col:
        start_col, end_col = st.columns(2)
        with start_col:
            override_start = st.text_input(
                "遅出",
                value=(pref.override_start_time if pref else None) or "",
                placeholder="10:00",
                key=f"{prefix}_start",
                label_visibility="collapsed",
            )
        with end_col:
            override_end = st.text_input(
                "早上がり",
                value=(pref.override_end_time if pref else None) or "",
                placeholder="13:00",
                key=f"{prefix}_end",
                label_visibility="collapsed",
            )
        note = st.text_input(
            "備考",
            value=(pref.note if pref else None) or "",
            placeholder="備考（通院・学校行事など）",
            key=f"{prefix}_note",
            label_visibility="collapsed",
        )

    return StaffDatePreferenceInput(
        staff_id=selected_staff_id,
        work_date=work_date,
        absolute_off=absolute_off,
        prefer_off=prefer_off,
        available_extra=available_extra,
        override_start_time=override_start.strip() or None,
        override_end_time=override_end.strip() or None,
        note=note.strip() or None,
    )


st.caption("開始時刻（遅出）・終了時刻（早上がり）は HH:MM。空欄なら通常の時刻を使います。")

with st.form("preference_form"):
    header = st.columns([1.3, 0.9, 1.4, 2.6, 2.0])
    header[0].markdown("**日付**")
    header[1].markdown("**通常**")
    header[2].markdown("**実効勤務時間**")
    header[3].markdown("**希望**")
    header[4].markdown("**遅出 / 早上がり・備考**")

    entered = [_day_inputs(work_date) for work_date in dates]
    submitted = st.form_submit_button("このスタッフの希望を保存", type="primary")

if submitted:
    errors = services.save_period_preferences(conn, selected_staff_id, dates, entered)
    if errors:
        show_errors(errors)
    else:
        changed = sum(1 for pref in entered if not pref.is_empty)
        st.session_state[FLASH_KEY] = (
            f"{name_by_id[selected_staff_id]} の希望を保存しました"
            f"（{len(dates)}日のうち通常と異なる日: {changed}日）。"
        )
        st.rerun()

# 通常勤務時間が未設定で実効勤務時間を確定できない日を警告する（保存は妨げない）。
# 紙からの転記を止めないため、エラーではなく警告にしている
unresolved = [c for c in conditions.values() if c.time_status == TIME_STATUS_UNSET]
if unresolved:
    st.warning(
        f"通常勤務時間が未設定のため、{len(unresolved)}日分の実効勤務時間を確定できません。"
        "「① スタッフ管理」で通常勤務時間を登録してください（時刻は自動で補完しません）。"
    )
