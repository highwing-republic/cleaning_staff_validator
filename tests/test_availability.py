import pytest

from src.availability import (
    AVAILABLE,
    INACTIVE,
    LOCKED_OFF,
    UNAVAILABLE_PREFERENCE,
    WEEKDAY_UNAVAILABLE,
    build_availability_map,
    count_available_staff,
    is_available,
    resolve_availability,
)
from src.models import LockedAssignmentInput, PreferenceInput, SchedulerInput, StaffInput


def _staff(**kw):
    base = dict(
        staff_id=1,
        staff_name="山田",
        role_id=3,
        daily_work_minutes=480,
        max_consecutive_days=5,
        weekday_availability={w: True for w in range(7)},
    )
    base.update(kw)
    return StaffInput(**base)


# 2026-10-01 is a Thursday (weekday index 3)
THURSDAY = "2026-10-01"


class TestResolveAvailability:
    def test_available_default(self):
        staff = _staff()
        assert resolve_availability(staff, THURSDAY) == AVAILABLE

    def test_inactive_takes_priority_over_everything(self):
        staff = _staff(active=False, weekday_availability={w: False for w in range(7)})
        result = resolve_availability(
            staff, THURSDAY, preference_type="UNAVAILABLE", locked_is_working=False
        )
        assert result == INACTIVE

    def test_weekday_unavailable(self):
        staff = _staff(weekday_availability={3: False})
        assert resolve_availability(staff, THURSDAY) == WEEKDAY_UNAVAILABLE

    def test_weekday_unavailable_over_locked_off(self):
        staff = _staff(weekday_availability={3: False})
        result = resolve_availability(staff, THURSDAY, locked_is_working=False)
        assert result == WEEKDAY_UNAVAILABLE

    def test_unavailable_preference(self):
        staff = _staff()
        result = resolve_availability(staff, THURSDAY, preference_type="UNAVAILABLE")
        assert result == UNAVAILABLE_PREFERENCE

    def test_unavailable_preference_over_locked_off(self):
        staff = _staff()
        result = resolve_availability(
            staff, THURSDAY, preference_type="UNAVAILABLE", locked_is_working=False
        )
        assert result == UNAVAILABLE_PREFERENCE

    def test_locked_off(self):
        staff = _staff()
        result = resolve_availability(staff, THURSDAY, locked_is_working=False)
        assert result == LOCKED_OFF

    def test_locked_working_stays_available(self):
        staff = _staff()
        result = resolve_availability(staff, THURSDAY, locked_is_working=True)
        assert result == AVAILABLE

    def test_prefer_off_does_not_change_availability(self):
        staff = _staff()
        result = resolve_availability(staff, THURSDAY, preference_type="PREFER_OFF")
        assert result == AVAILABLE

    def test_prefer_work_does_not_change_availability(self):
        staff = _staff()
        result = resolve_availability(staff, THURSDAY, preference_type="PREFER_WORK")
        assert result == AVAILABLE

    def test_locked_none_defaults_available(self):
        staff = _staff()
        assert resolve_availability(staff, THURSDAY, locked_is_working=None) == AVAILABLE


class TestIsAvailable:
    @pytest.mark.parametrize(
        "reason,expected",
        [
            (AVAILABLE, True),
            (INACTIVE, False),
            (WEEKDAY_UNAVAILABLE, False),
            (UNAVAILABLE_PREFERENCE, False),
            (LOCKED_OFF, False),
        ],
    )
    def test_is_available(self, reason, expected):
        assert is_available(reason) is expected


class TestBuildAvailabilityMap:
    def test_covers_all_staff_and_dates_31_days(self):
        staff_list = [_staff(staff_id=1), _staff(staff_id=2, active=False)]
        scheduler_input = SchedulerInput(year_month="2026-10", staff=staff_list)
        result = build_availability_map(scheduler_input)
        assert len(result) == 2 * 31
        for day in range(1, 32):
            date_str = f"2026-10-{day:02d}"
            assert (1, date_str) in result
            assert (2, date_str) in result
        assert result[(2, "2026-10-01")] == INACTIVE

    def test_covers_all_staff_and_dates_30_days(self):
        staff_list = [_staff(staff_id=1)]
        scheduler_input = SchedulerInput(year_month="2026-11", staff=staff_list)
        result = build_availability_map(scheduler_input)
        assert len(result) == 30

    def test_preferences_and_locks_indexed_correctly(self):
        staff_list = [_staff(staff_id=1)]
        preferences = [PreferenceInput(staff_id=1, work_date=THURSDAY, preference_type="UNAVAILABLE")]
        locked = [LockedAssignmentInput(staff_id=1, work_date="2026-10-02", is_working=False)]
        scheduler_input = SchedulerInput(
            year_month="2026-10",
            staff=staff_list,
            preferences=preferences,
            locked_assignments=locked,
        )
        result = build_availability_map(scheduler_input)
        assert result[(1, THURSDAY)] == UNAVAILABLE_PREFERENCE
        assert result[(1, "2026-10-02")] == LOCKED_OFF
        assert result[(1, "2026-10-03")] == AVAILABLE


class TestCountAvailableStaff:
    def test_count_without_role(self):
        availability_map = {
            (1, THURSDAY): AVAILABLE,
            (2, THURSDAY): AVAILABLE,
            (3, THURSDAY): INACTIVE,
            (1, "2026-10-02"): AVAILABLE,
        }
        assert count_available_staff(availability_map, THURSDAY) == 2

    def test_count_with_role(self):
        staff_list = [
            _staff(staff_id=1, role_id=1),
            _staff(staff_id=2, role_id=2),
            _staff(staff_id=3, role_id=1),
        ]
        availability_map = {
            (1, THURSDAY): AVAILABLE,
            (2, THURSDAY): AVAILABLE,
            (3, THURSDAY): WEEKDAY_UNAVAILABLE,
        }
        assert count_available_staff(availability_map, THURSDAY, role_id=1, staff=staff_list) == 1
        assert count_available_staff(availability_map, THURSDAY, role_id=2, staff=staff_list) == 1

    def test_role_id_without_staff_raises(self):
        availability_map = {(1, THURSDAY): AVAILABLE}
        with pytest.raises(ValueError):
            count_available_staff(availability_map, THURSDAY, role_id=1)

    def test_count_zero_when_none_available(self):
        availability_map = {(1, THURSDAY): INACTIVE}
        assert count_available_staff(availability_map, THURSDAY) == 0
