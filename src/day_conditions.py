"""通常勤務条件（スタッフマスター）と期間別勤務希望から、ある1日の実効条件を求める.

将来Solverがこの結果を使うため、DB・Streamlitに依存しない純粋関数にしている。

考え方:
    スタッフマスター = 普段どう働く人か
    勤務希望         = 今回だけ何が違うか
勤務希望のない日は通常条件がそのまま実効条件になる（行がない = 通常）。
"""

from src.constants import (
    TIME_STATUS_INVALID,
    TIME_STATUS_NOT_APPLICABLE,
    TIME_STATUS_OK,
    TIME_STATUS_UNSET,
)
from src.models import StaffDatePreferenceInput, StaffDayCondition, StaffDetail
from src.period_utils import parse_date
from src.work_time import normalize_hhmm, parse_hhmm


def _clean_note(note: str | None) -> str | None:
    if not isinstance(note, str):
        return None
    return note.strip() or None


def resolve_staff_day_condition(
    staff: StaffDetail,
    work_date: str,
    preference: StaffDatePreferenceInput | None = None,
) -> StaffDayCondition:
    """スタッフの通常条件と対象日の勤務希望から実効条件を求める.

    判定順:
    1. ABSOLUTE_OFF があれば最優先で勤務不可
    2. 通常勤務曜日なら勤務可能
    3. 通常休み曜日でも AVAILABLE_EXTRA があれば勤務可能
    PREFER_OFF は can_work を変えない。
    EARLY_LEAVE / LATE_START（override_*）も出勤を強制しない。
    """
    day = parse_date(work_date)
    if day is None:
        raise ValueError(f"work_date must be YYYY-MM-DD: {work_date!r}")

    base_available = day.weekday() in set(staff.weekdays)

    absolute_off = bool(preference and preference.absolute_off)
    prefer_off = bool(preference and preference.prefer_off)
    available_extra = bool(preference and preference.available_extra)

    if absolute_off:
        can_work = False
    elif base_available:
        can_work = True
    else:
        can_work = available_extra

    start, end, time_status, start_overridden, end_overridden = _resolve_times(
        staff, preference, can_work
    )

    return StaffDayCondition(
        staff_id=staff.staff.staff_id,
        work_date=day.isoformat(),
        base_available=base_available,
        can_work=can_work,
        prefer_off=prefer_off,
        available_extra=available_extra,
        absolute_off=absolute_off,
        effective_start_time=start,
        effective_end_time=end,
        time_status=time_status,
        start_overridden=start_overridden,
        end_overridden=end_overridden,
        note=_clean_note(preference.note if preference else None),
        has_preference=preference is not None,
    )


def _resolve_times(
    staff: StaffDetail,
    preference: StaffDatePreferenceInput | None,
    can_work: bool,
) -> tuple[str | None, str | None, str, bool, bool]:
    """実効勤務時刻と、通常勤務時刻を上書きしたかを求める.

    override があればそれを、なければ通常勤務時刻を使う（勝手な時刻は補完しない）。
    勤務不可の日は実効時間を持たない。
    """
    if not can_work:
        return None, None, TIME_STATUS_NOT_APPLICABLE, False, False

    override_start = normalize_hhmm(preference.override_start_time) if preference else None
    override_end = normalize_hhmm(preference.override_end_time) if preference else None

    start = override_start or normalize_hhmm(staff.staff.standard_start_time)
    end = override_end or normalize_hhmm(staff.staff.standard_end_time)
    start_overridden = override_start is not None
    end_overridden = override_end is not None

    if start is None or end is None:
        # 通常勤務時間が未設定で、overrideだけでは確定できない
        return start, end, TIME_STATUS_UNSET, start_overridden, end_overridden

    if parse_hhmm(end) <= parse_hhmm(start):
        return start, end, TIME_STATUS_INVALID, start_overridden, end_overridden

    return start, end, TIME_STATUS_OK, start_overridden, end_overridden


def resolve_period_conditions(
    staff: StaffDetail,
    work_dates: list[str],
    preferences: dict[str, StaffDatePreferenceInput] | None = None,
) -> list[StaffDayCondition]:
    """対象期間の各日について実効条件を求める. preferences は work_date をキーにする."""
    by_date = preferences or {}
    return [
        resolve_staff_day_condition(staff, work_date, by_date.get(work_date))
        for work_date in work_dates
    ]
