"""日本の祝日（表示用）.

勤務表や入力画面の日付に「祝」を付け、土日と同じように色を付けるために使う。
祝日の判定は jpholiday（祝日法の規則で計算. 振替休日・国民の休日を含む）に任せる。
jpholiday が入っていない環境（古い PC 版など）では、祝日なしとして動く（画面は落とさない）。

ここでは表示だけを扱う。必要人数の初期値などの判定には使っていない。
"""

from datetime import date
from functools import lru_cache

try:  # pragma: no cover - 入っていない環境のための保険
    import jpholiday as _jpholiday
except ImportError:  # pragma: no cover
    _jpholiday = None

DAY_COLOR_SATURDAY = "blue"
DAY_COLOR_HOLIDAY = "red"  # 日曜・祝日


def _to_date(work_date: str | date) -> date | None:
    if isinstance(work_date, date):
        return work_date
    try:
        return date.fromisoformat(str(work_date).strip())
    except ValueError:
        return None


@lru_cache(maxsize=4096)
def _holiday_name(day: date) -> str | None:
    if _jpholiday is None:
        return None
    return _jpholiday.is_holiday_name(day) or None


def holiday_name(work_date: str | date) -> str | None:
    """祝日なら名前（'文化の日' など）、祝日でなければ None."""
    day = _to_date(work_date)
    return _holiday_name(day) if day else None


def is_holiday(work_date: str | date) -> bool:
    return holiday_name(work_date) is not None


def day_color(work_date: str | date) -> str | None:
    """日付の色: 日曜・祝日は 'red'、土曜は 'blue'、平日は None.

    土曜の祝日は祝日（赤）を優先する。
    """
    day = _to_date(work_date)
    if day is None:
        return None
    if day.weekday() == 6 or is_holiday(day):
        return DAY_COLOR_HOLIDAY
    if day.weekday() == 5:
        return DAY_COLOR_SATURDAY
    return None
