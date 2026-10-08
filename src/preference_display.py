"""期間別勤務希望の表示用文字列（グリッド・編集画面共通）.

「変更なしの日」は空欄や「未入力」ではなく「通常」と表示する。
勤務希望の行がないのは入力漏れではなく「通常条件を使う」という意味のため。
"""

from src.constants import (
    TIME_STATUS_INVALID,
    TIME_STATUS_UNSET,
)
from src.models import StaffDayCondition
# 曜日ラベルと短縮日付は期間系の共通表示なので period_utils に集約している
from src.period_utils import WEEKDAY_LABELS_JA, format_date_short
from src.work_time import format_standard_work_time

__all__ = [
    "ABSOLUTE_OFF_LABEL",
    "AVAILABLE_EXTRA_LABEL",
    "NORMAL_LABEL",
    "NORMAL_OFF_LABEL",
    "NOTE_MARK",
    "PREFER_OFF_LABEL",
    "TIME_INVALID_LABEL",
    "TIME_UNSET_LABEL",
    "WEEKDAY_LABELS_JA",
    "format_base_availability",
    "format_date_short",
    "format_day_condition",
    "format_effective_time",
]

NORMAL_LABEL = "通常"
NORMAL_OFF_LABEL = "通常休み"
ABSOLUTE_OFF_LABEL = "絶休"
PREFER_OFF_LABEL = "希休"
AVAILABLE_EXTRA_LABEL = "勤務可"
TIME_UNSET_LABEL = "時間未設定"
TIME_INVALID_LABEL = "時間矛盾"
# 備考がある日の目印（備考自体は条件ではないが、若女将が気づけるようにする）
NOTE_MARK = "＊"

def format_base_availability(condition: StaffDayCondition) -> str:
    """その日が通常勤務曜日か通常休み曜日かの表示."""
    return NORMAL_LABEL if condition.base_available else NORMAL_OFF_LABEL


def format_day_condition(condition: StaffDayCondition) -> str:
    """グリッド1セル分の表示.

    例: '通常' / '絶休' / '希休' / '13:00まで' / '10:00から' / '10:00-13:00' /
        '通常休み' / '勤務可 09:00-15:30'
    """
    parts: list[str] = []
    if condition.absolute_off:
        parts.append(ABSOLUTE_OFF_LABEL)
    elif condition.prefer_off:
        parts.append(PREFER_OFF_LABEL)

    if not condition.absolute_off and not condition.base_available:
        parts.append(AVAILABLE_EXTRA_LABEL if condition.available_extra else NORMAL_OFF_LABEL)

    time_text = _format_time_change(condition)
    if time_text:
        parts.append(time_text)

    if condition.time_status == TIME_STATUS_UNSET and condition.can_work:
        parts.append(TIME_UNSET_LABEL)
    elif condition.time_status == TIME_STATUS_INVALID:
        parts.append(TIME_INVALID_LABEL)

    text = " ".join(parts) if parts else NORMAL_LABEL
    if condition.note:
        text += NOTE_MARK
    return text


def _format_time_change(condition: StaffDayCondition) -> str:
    """通常勤務時刻を上書きした場合だけ表示する（通常どおりなら空文字）.

    変更がない日を「通常」と表示するため、通常勤務時刻はここでは出さない。
    """
    if not condition.can_work:
        return ""
    if not (condition.start_overridden or condition.end_overridden):
        return ""
    start, end = condition.effective_start_time, condition.effective_end_time
    if condition.start_overridden and condition.end_overridden:
        return f"{start}-{end}"
    if condition.end_overridden:
        return f"{end}まで"
    return f"{start}から"


def format_effective_time(condition: StaffDayCondition) -> str:
    """編集画面で見せる実効勤務時間（未確定なら理由を出す）."""
    if not condition.can_work:
        return "勤務なし"
    if condition.time_status == TIME_STATUS_UNSET:
        return "通常勤務時間が未設定です"
    return format_standard_work_time(
        condition.effective_start_time, condition.effective_end_time
    )
