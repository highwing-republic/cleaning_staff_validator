"""勤務希望の入力部品（普段と違う日だけを1日ずつ登録する）.

何も登録しなければ、スタッフ設定の通常の曜日・時刻のまま勤務表を作る。
紙に書かれた「普段と違う日」だけを、スタッフと日付を選んで登録する。
時刻は通常の勤務時刻を初期値にしたプルダウンで、変えたときだけ遅出・早上がりとして保存する。

画面に依存しない部分（保存する値の組み立て・一覧の表示文）は関数に分けてある。
"""

from collections.abc import Callable
import sqlite3

import streamlit as st

from src import repositories as repo
from src import services
from src.models import StaffDatePreferenceInput, StaffDayCondition, StaffDetail
from src.period_utils import format_date_short
from src.time_select import time_select
from src.ui_common import show_errors

OFF_NONE = "休みの希望なし"
OFF_ABSOLUTE = "必ず休み"
OFF_PREFER = "できれば休み"
OFF_CHOICES: tuple[str, ...] = (OFF_NONE, OFF_ABSOLUTE, OFF_PREFER)

EDIT_BUTTON = "直す"
RESET_BUTTON = "通常に戻す"


# ---------------------------------------------------------------------------
# 画面に依存しない部分
# ---------------------------------------------------------------------------


def off_choice_of(pref: StaffDatePreferenceInput | None) -> str:
    """保存済みの希望から「休み」の選択肢を決める（絶対休みを優先）."""
    if pref is None:
        return OFF_NONE
    if pref.absolute_off:
        return OFF_ABSOLUTE
    if pref.prefer_off:
        return OFF_PREFER
    return OFF_NONE


def build_preference(
    staff_id: int,
    work_date: str,
    off_choice: str,
    available_extra: bool,
    start_time: str | None,
    end_time: str | None,
    note: str,
    *,
    standard_start: str | None,
    standard_end: str | None,
    existing: StaffDatePreferenceInput | None = None,
) -> StaffDatePreferenceInput:
    """入力から保存する希望を作る.

    - 時刻は通常の時刻から変えたときだけ遅出・早上がりとして持つ
    - 必ず休みの日は時刻と「普段休みの日に勤務できる」を持たない（両立しないため）
    - 絶対休みとできれば休みの併用は仕様上ありうるため、既存の「できれば休み」は
      必ず休みを選び直しても消さない
    """
    absolute = off_choice == OFF_ABSOLUTE
    prefer = off_choice == OFF_PREFER or (
        absolute and existing is not None and existing.prefer_off
    )
    if absolute:
        override_start = override_end = None
        extra = False
    else:
        override_start = start_time if start_time and start_time != standard_start else None
        override_end = end_time if end_time and end_time != standard_end else None
        extra = available_extra
    return StaffDatePreferenceInput(
        staff_id=staff_id,
        work_date=work_date,
        absolute_off=absolute,
        prefer_off=prefer,
        available_extra=extra,
        override_start_time=override_start,
        override_end_time=override_end,
        note=note.strip() or None,
    )


def summarize_preference(pref: StaffDatePreferenceInput) -> str:
    """登録済み一覧の「内容」. 例 '必ず休み' / 'できれば休み・13:00まで（早上がり）'."""
    parts: list[str] = []
    if pref.absolute_off:
        parts.append(OFF_ABSOLUTE)
    elif pref.prefer_off:
        parts.append(OFF_PREFER)
    if pref.available_extra:
        parts.append("普段休みの日に勤務できる")
    start, end = pref.override_start_time, pref.override_end_time
    if start and end:
        parts.append(f"{start}-{end}")
    elif end:
        parts.append(f"{end}まで（早上がり）")
    elif start:
        parts.append(f"{start}から（遅出）")
    if not parts:
        return "メモのみ" if pref.note else "通常"
    return "・".join(parts)


def save_button_label(work_date: str) -> str:
    """保存ボタンの文言. 「この日」ではなく日付を書く（どの日を保存するか迷わないように）."""
    return f"{format_date_short(work_date)}の希望を保存"


def describe_normal_day(condition: StaffDayCondition, detail: StaffDetail) -> str:
    """選んだスタッフ・日付の「普段」の説明（例 '太郎の 11/1(日) は、普段は休みの曜日です。'）."""
    subject = f"{detail.staff.staff_name}の {format_date_short(condition.work_date)} は、"
    if not condition.base_available:
        return f"{subject}普段は休みの曜日です。"
    start, end = detail.staff.standard_start_time, detail.staff.standard_end_time
    if start and end:
        return f"{subject}普段どおりなら {start}-{end} の勤務です。"
    return f"{subject}普段の勤務日です（通常勤務時間は未設定）。"


# ---------------------------------------------------------------------------
# 画面
# ---------------------------------------------------------------------------


def _select_for_edit(key: str, staff_id: int, work_date: str) -> None:
    """一覧の「直す」: 入力欄のスタッフと日付をその行に合わせる（次の描画の前に呼ばれる）."""
    st.session_state[f"{key}_staff"] = staff_id
    st.session_state[f"{key}_date"] = work_date
    st.session_state[f"{key}_open"] = True


def _save(
    conn: sqlite3.Connection,
    pref: StaffDatePreferenceInput,
    staff_name: str,
    on_saved: Callable[[str], None],
) -> None:
    errors = services.save_period_preferences(conn, pref.staff_id, [pref.work_date], [pref])
    if errors:
        show_errors(errors)
        return
    day = format_date_short(pref.work_date)
    if pref.is_empty:
        on_saved(f"{staff_name} の {day} を通常に戻しました。")
    else:
        on_saved(f"{staff_name} の {day} の希望を保存しました。ほかの日の希望もあれば続けて登録してください。")
    st.rerun()


def _registered_list(
    conn: sqlite3.Connection,
    prefs: list[StaffDatePreferenceInput],
    names: dict[int, str],
    key: str,
    on_saved: Callable[[str], None],
    empty_message: str,
) -> None:
    if not prefs:
        st.info(empty_message)
        return

    st.caption(f"登録済みの希望（{len(prefs)}件）。ここにない日は普段どおりです。")
    header = st.columns([2.0, 1.3, 3.0, 2.0, 1.0, 1.9])
    for col, label in zip(header, ("スタッフ", "日付", "内容", "メモ", "", "")):
        col.markdown(f"**{label}**" if label else "")
    for pref in sorted(prefs, key=lambda p: (p.work_date, names.get(p.staff_id, ""))):
        name = names.get(pref.staff_id, str(pref.staff_id))
        cols = st.columns([2.0, 1.3, 3.0, 2.0, 1.0, 1.9])
        cols[0].write(name)
        cols[1].write(format_date_short(pref.work_date))
        cols[2].write(summarize_preference(pref))
        cols[3].write(pref.note or "")
        cols[4].button(
            EDIT_BUTTON,
            key=f"{key}_edit_{pref.staff_id}_{pref.work_date}",
            on_click=_select_for_edit,
            args=(key, pref.staff_id, pref.work_date),
        )
        if cols[5].button(RESET_BUTTON, key=f"{key}_reset_{pref.staff_id}_{pref.work_date}"):
            _save(conn, StaffDatePreferenceInput(pref.staff_id, pref.work_date), name, on_saved)


def _entry_form(
    conn: sqlite3.Connection,
    dates: list[str],
    staff_details: list[StaffDetail],
    prefs: list[StaffDatePreferenceInput],
    key: str,
    on_saved: Callable[[str], None],
) -> None:
    details = {d.staff.staff_id: d for d in staff_details}
    names = {sid: d.staff.staff_name for sid, d in details.items()}

    left, right = st.columns(2)
    with left:
        staff_id = st.selectbox(
            "スタッフ", list(names), format_func=names.get, key=f"{key}_staff"
        )
    with right:
        work_date = st.selectbox(
            "日付", dates, format_func=format_date_short, key=f"{key}_date"
        )

    detail = details[staff_id]
    existing = next(
        (p for p in prefs if p.staff_id == staff_id and p.work_date == work_date), None
    )
    condition = {
        c.work_date: c for c in services.get_period_conditions(conn, detail, [work_date])
    }[work_date]
    standard_start = detail.staff.standard_start_time
    standard_end = detail.staff.standard_end_time

    day = format_date_short(work_date)
    st.info(describe_normal_day(condition, detail))
    if not (standard_start and standard_end):
        st.warning(
            f"{names[staff_id]}は通常勤務時間が未設定です。"
            "「スタッフ設定」で登録すると、遅出・早上がりの時刻が確定します。"
        )

    # スタッフ・日付ごとに入力欄を分け、選び直したときに前の入力が残らないようにする
    prefix = f"{key}_{staff_id}_{work_date}"
    off_choice = st.radio(
        f"{day}の休み",
        OFF_CHOICES,
        index=OFF_CHOICES.index(off_choice_of(existing)),
        horizontal=True,
        key=f"{prefix}_off",
    )

    available_extra = False
    start_time = end_time = None
    if off_choice != OFF_ABSOLUTE:
        if not condition.base_available:
            available_extra = st.checkbox(
                f"{day}は勤務できる（普段は休みの曜日）",
                value=bool(existing and existing.available_extra),
                key=f"{prefix}_extra",
            )
        st.caption(
            f"{day}の勤務時間は、普段どおりなら変えなくて構いません。"
            "遅出・早上がりのときだけ変えます。"
        )
        has_standard = bool(standard_start and standard_end)
        time_left, time_right = st.columns(2)
        with time_left:
            start_time = time_select(
                "開始時刻",
                (existing.override_start_time if existing else None) or standard_start,
                key=f"{prefix}_start",
                allow_unset=not has_standard,
            )
        with time_right:
            end_time = time_select(
                "終了時刻",
                (existing.override_end_time if existing else None) or standard_end,
                key=f"{prefix}_end",
                allow_unset=not has_standard,
            )

    note = st.text_input(
        "メモ（任意）",
        value=(existing.note if existing else None) or "",
        placeholder="通院・学校行事など",
        key=f"{prefix}_note",
    )

    if st.button(save_button_label(work_date), type="primary", key=f"{key}_save"):
        pref = build_preference(
            staff_id,
            work_date,
            off_choice,
            available_extra,
            start_time,
            end_time,
            note,
            standard_start=standard_start,
            standard_end=standard_end,
            existing=existing,
        )
        _save(conn, pref, names[staff_id], on_saved)
    st.caption(f"何も変えずに保存すると、{day}は普段どおりに戻ります。")


def render_preference_entry(
    conn: sqlite3.Connection,
    dates: list[str],
    staff_details: list[StaffDetail],
    *,
    key: str,
    on_saved: Callable[[str], None],
    empty_message: str,
    form_label: str = "休み・勤務時間の希望を登録／直す",
) -> None:
    """登録済みの希望の一覧と、1日分の入力欄を表示する."""
    if not staff_details or not dates:
        return
    prefs = repo.list_preferences_in_period(conn, dates[0], dates[-1])
    names = {d.staff.staff_id: d.staff.staff_name for d in staff_details}

    _registered_list(conn, prefs, names, key, on_saved, empty_message)
    with st.expander(form_label, expanded=bool(st.session_state.get(f"{key}_open"))):
        _entry_form(conn, dates, staff_details, prefs, key, on_saved)
