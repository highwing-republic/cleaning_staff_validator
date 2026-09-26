"""年月・日付関連のユーティリティ（§15, §33）."""

import calendar
import re
from datetime import date

_YEAR_MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")


def parse_year_month(year_month: str) -> tuple[int, int]:
    """'YYYY-MM' を (year, month) に変換する. 不正な形式は ValueError."""
    if not isinstance(year_month, str):
        raise ValueError(f"year_month must be str: {year_month!r}")
    match = _YEAR_MONTH_PATTERN.match(year_month)
    if match is None:
        raise ValueError(f"year_month must be YYYY-MM: {year_month!r}")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {year_month!r}")
    if year < 1:
        raise ValueError(f"year out of range: {year_month!r}")
    return year, month


def get_month_dates(year_month: str) -> list[str]:
    """対象月の全日付を 'YYYY-MM-DD' 形式で昇順に返す."""
    year, month = parse_year_month(year_month)
    days = calendar.monthrange(year, month)[1]
    return [date(year, month, day).isoformat() for day in range(1, days + 1)]


def weekday_index(work_date: str | date) -> int:
    """曜日番号を返す（0=Monday ... 6=Sunday, §20）."""
    if isinstance(work_date, date):
        return work_date.weekday()
    return date.fromisoformat(work_date).weekday()


def round_half_up_workdays(target_monthly_minutes: int, daily_work_minutes: int) -> int:
    """所定勤務分を勤務日数に換算する（端数は四捨五入, §33）."""
    if daily_work_minutes <= 0:
        raise ValueError(f"daily_work_minutes must be positive: {daily_work_minutes}")
    if target_monthly_minutes < 0:
        raise ValueError(f"target_monthly_minutes must be >= 0: {target_monthly_minutes}")
    return (target_monthly_minutes + daily_work_minutes // 2) // daily_work_minutes
