"""時刻のプルダウン入力（「時」は1時間刻み、「分」は 00 / 30 から選ぶ）.

手入力の "9:00" / "0900" のような書き間違いをなくすため、時刻は選ぶだけにする。
保存済みの値が選択肢にない場合（例 09:15、4時台）は、その値を選択肢に加えて
編集画面を開いただけで値が変わらないようにする。

選択肢の組み立て・時刻の分解と結合はStreamlitに依存しない関数にしてある。
"""

import streamlit as st

from src.work_time import normalize_hhmm

# 清掃の勤務帯として選べる時（5時〜23時）
HOUR_CHOICES: tuple[int, ...] = tuple(range(5, 24))
# 分はふだん 00 分。必要なときだけ 30 分を選ぶ
MINUTE_CHOICES: tuple[str, ...] = ("00", "30")
DEFAULT_MINUTE = "00"
UNSET_LABEL = "未設定"


def split_time(value: object) -> tuple[int | None, str]:
    """'HH:MM' を (時, 分) にする. 未設定・不正な値は (None, '00')."""
    normalized = normalize_hhmm(value) if value else None
    if normalized is None:
        return None, DEFAULT_MINUTE
    hour, minute = normalized.split(":")
    return int(hour), minute


def join_time(hour: int | None, minute: str) -> str | None:
    """(時, 分) を 'HH:MM' にする. 時が未設定なら None."""
    if hour is None:
        return None
    return f"{hour:02d}:{minute}"


def hour_options(current: object, allow_unset: bool = True) -> list[int | None]:
    """時の選択肢. 保存済みの時が範囲外なら加える."""
    hours = list(HOUR_CHOICES)
    current_hour, _ = split_time(current)
    if current_hour is not None and current_hour not in hours:
        hours = sorted([*hours, current_hour])
    return [None, *hours] if allow_unset else hours


def minute_options(current: object) -> list[str]:
    """分の選択肢. 保存済みの分が 00 / 30 以外なら加える."""
    minutes = list(MINUTE_CHOICES)
    _, current_minute = split_time(current)
    if current_minute not in minutes:
        minutes = sorted([*minutes, current_minute])
    return minutes


def format_hour(hour: int | None) -> str:
    return UNSET_LABEL if hour is None else f"{hour}時"


def format_minute(minute: str) -> str:
    return f"{minute}分"


def time_select(
    label: str,
    value: str | None,
    key: str,
    *,
    allow_unset: bool = True,
    help: str | None = None,
) -> str | None:
    """「時」と「分」の2つのプルダウンで時刻を選ぶ. 'HH:MM' か None を返す.

    widget の key は f"{key}_hour" / f"{key}_minute"。
    """
    hours = hour_options(value, allow_unset=allow_unset)
    minutes = minute_options(value)
    current_hour, current_minute = split_time(value)
    if current_hour is None and not allow_unset:
        current_hour = hours[0]

    hour_col, minute_col = st.columns([3, 2])
    with hour_col:
        hour = st.selectbox(
            label,
            options=hours,
            index=hours.index(current_hour),
            format_func=format_hour,
            key=f"{key}_hour",
            help=help,
        )
    with minute_col:
        minute = st.selectbox(
            "分",
            options=minutes,
            index=minutes.index(current_minute),
            format_func=format_minute,
            key=f"{key}_minute",
        )
    return join_time(hour, minute)
