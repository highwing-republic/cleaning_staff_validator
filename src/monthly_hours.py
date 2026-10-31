"""月ごとの合計勤務時間（スタッフ単位）.

勤務表をつくるときに、その月にあと何時間入っているかが分かるようにする。
期間（最大14日）は月をまたげるので、期間が含む月それぞれについて、
  その月の保存済みの勤務（期間の外の日）＋ いま見ている期間の勤務（保存済み、または新しい勤務案）
を合計する。
時間は勤務表の開始〜終了の時間で数える（休憩は差し引かない。アプリに休憩の設定がないため）。

画面に依存しない。
"""

from calendar import monthrange
from collections.abc import Iterable
from dataclasses import dataclass
import sqlite3

from src import services
from src.work_time import standard_work_minutes


@dataclass(frozen=True)
class WorkedShift:
    staff_id: int
    work_date: str
    start_time: str | None
    end_time: str | None


@dataclass(frozen=True)
class MonthTotal:
    days: int = 0
    minutes: int = 0

    def __add__(self, other: "MonthTotal") -> "MonthTotal":
        return MonthTotal(self.days + other.days, self.minutes + other.minutes)


def months_of(dates: Iterable[str]) -> list[str]:
    """期間が含む月（'YYYY-MM'）を古い順に."""
    return sorted({d[:7] for d in dates})


def month_label(month: str) -> str:
    return f"{int(month[5:7])}月"


def month_dates(month: str) -> list[str]:
    year, mon = int(month[:4]), int(month[5:7])
    return [f"{month}-{day:02d}" for day in range(1, monthrange(year, mon)[1] + 1)]


def shift_minutes(shift: WorkedShift) -> int:
    """1回の勤務の分（開始〜終了）. 時刻が不正なら 0."""
    return standard_work_minutes(shift.start_time, shift.end_time) or 0


def totals_by_month(shifts: Iterable[WorkedShift]) -> dict[int, dict[str, MonthTotal]]:
    """staff_id -> 月 -> 合計."""
    totals: dict[int, dict[str, MonthTotal]] = {}
    for shift in shifts:
        month = shift.work_date[:7]
        by_month = totals.setdefault(shift.staff_id, {})
        by_month[month] = by_month.get(month, MonthTotal()) + MonthTotal(1, shift_minutes(shift))
    return totals


def saved_shifts(conn: sqlite3.Connection, dates: Iterable[str]) -> list[WorkedShift]:
    """保存済みの勤務表から、出勤の日だけを取り出す."""
    dates = sorted(dates)
    if not dates:
        return []
    schedule = services.get_current_schedule(conn, dates)
    return [
        WorkedShift(staff_id, view.work_date, a.start_time, a.end_time)
        for view in schedule.days
        if view.exists
        for staff_id, a in view.assignments.items()
        if a.is_working
    ]


def monthly_totals(
    conn: sqlite3.Connection,
    period_dates: list[str],
    period_shifts: Iterable[WorkedShift] | None = None,
) -> dict[int, dict[str, MonthTotal]]:
    """期間が含む月ごとの合計（staff_id -> 月 -> 合計）.

    period_shifts を渡すと、期間内の日はそれ（新しい勤務案など）で数え、期間の外の日は保存済みで数える。
    渡さなければ、期間内も保存済みの勤務表で数える。
    """
    period = set(period_dates)
    month_days = [d for month in months_of(period_dates) for d in month_dates(month)]
    if period_shifts is None:
        shifts = saved_shifts(conn, month_days)
    else:
        outside = [d for d in month_days if d not in period]
        shifts = saved_shifts(conn, outside) + [s for s in period_shifts if s.work_date in period]
    return totals_by_month(shifts)


def format_hours(minutes: int) -> str:
    """'110.5時間' のように（15分刻みなら小数2桁まで. 0は '0時間'）."""
    hours = minutes / 60
    text = f"{hours:.2f}".rstrip("0").rstrip(".")
    return f"{text}時間"


def format_month_total(total: MonthTotal | None) -> str:
    """'110.5時間（17日）'. その月に勤務がなければ '0時間'."""
    if total is None or total.days == 0:
        return "0時間"
    return f"{format_hours(total.minutes)}（{total.days}日）"
