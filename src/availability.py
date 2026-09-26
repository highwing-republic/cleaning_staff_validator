"""勤務可能性判定（§12, §29 HC01/HC02/HC12, T23）.

判定順（§23 Phase 5）：
inactive → weekday unavailable → UNAVAILABLE preference → locked off → available
"""

from src.constants import PREFERENCE_UNAVAILABLE
from src.models import SchedulerInput, StaffInput
from src.month_utils import get_month_dates, weekday_index

AVAILABLE = "AVAILABLE"
INACTIVE = "INACTIVE"
WEEKDAY_UNAVAILABLE = "WEEKDAY_UNAVAILABLE"
UNAVAILABLE_PREFERENCE = "UNAVAILABLE_PREFERENCE"
LOCKED_OFF = "LOCKED_OFF"


def resolve_availability(
    staff: StaffInput,
    work_date: str,
    preference_type: str | None = None,
    locked_is_working: bool | None = None,
) -> str:
    """1名・1日の勤務可能性理由を判定する.

    ロックが勤務(True)、または PREFER_OFF/PREFER_WORK の希望は
    可否を変えない（Hard Constraintではないため）。
    """
    if not staff.active:
        return INACTIVE

    weekday = weekday_index(work_date)
    if not staff.is_available_on(weekday):
        return WEEKDAY_UNAVAILABLE

    if preference_type == PREFERENCE_UNAVAILABLE:
        return UNAVAILABLE_PREFERENCE

    if locked_is_working is False:
        return LOCKED_OFF

    return AVAILABLE


def build_availability_map(scheduler_input: SchedulerInput) -> dict[tuple[int, str], str]:
    """全スタッフ × 対象月全日の勤務可能性マップを作る."""
    dates = get_month_dates(scheduler_input.year_month)

    preference_index: dict[tuple[int, str], str] = {
        (p.staff_id, p.work_date): p.preference_type for p in scheduler_input.preferences
    }
    locked_index: dict[tuple[int, str], bool] = {
        (a.staff_id, a.work_date): a.is_working for a in scheduler_input.locked_assignments
    }

    availability_map: dict[tuple[int, str], str] = {}
    for staff in scheduler_input.staff:
        for work_date in dates:
            key = (staff.staff_id, work_date)
            availability_map[key] = resolve_availability(
                staff,
                work_date,
                preference_type=preference_index.get(key),
                locked_is_working=locked_index.get(key),
            )
    return availability_map


def is_available(reason: str) -> bool:
    """勤務可能を表す理由か."""
    return reason == AVAILABLE


def count_available_staff(
    availability_map: dict[tuple[int, str], str],
    work_date: str,
    role_id: int | None = None,
    staff: list[StaffInput] | None = None,
) -> int:
    """指定日の勤務可能人数を数える（PC05/PC06用）.

    role_id を指定する場合は staff（ロール参照用）が必須。
    """
    if role_id is not None and staff is None:
        raise ValueError("role_id を指定する場合は staff が必要です。")

    role_by_staff_id: dict[int, int] = {}
    if role_id is not None and staff is not None:
        role_by_staff_id = {s.staff_id: s.role_id for s in staff}

    count = 0
    for (staff_id, date), reason in availability_map.items():
        if date != work_date or not is_available(reason):
            continue
        if role_id is not None and role_by_staff_id.get(staff_id) != role_id:
            continue
        count += 1
    return count
