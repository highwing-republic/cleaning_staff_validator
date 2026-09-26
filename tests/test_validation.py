import pytest

from src.models import (
    DailyRequirementInput,
    MonthlyConditionInput,
    PreferenceInput,
    RoleRequirementInput,
    StaffInput,
)
from src.validation import (
    MONTHLY_CONDITION_CARRYOVER_EXCEEDS_MAX,
    MONTHLY_CONDITION_CARRYOVER_NEGATIVE,
    MONTHLY_CONDITION_MIN_EXCEEDS_MAX,
    MONTHLY_CONDITION_MIN_EXCEEDS_TARGET,
    MONTHLY_CONDITION_TARGET_EXCEEDS_MAX,
    PREFERENCE_PREFER_WORK_ON_UNAVAILABLE_WEEKDAY,
    PREFERENCE_STAFF_ID_MISMATCH,
    REQUIREMENT_REQUIRED_EXCEEDS_MAX,
    REQUIREMENT_ROLE_SUM_EXCEEDS_MAX,
    REQUIREMENT_ROLE_SUM_EXCEEDS_REQUIRED,
    STAFF_DAILY_WORK_MINUTES_INVALID,
    STAFF_MAX_CONSECUTIVE_DAYS_INVALID,
    STAFF_NAME_REQUIRED,
    STAFF_SKILL_LEVEL_INVALID,
    validate_daily_requirement,
    validate_monthly_condition,
    validate_preference,
    validate_staff,
)


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


# ---------------------------------------------------------------------------
# T19 validate_staff
# ---------------------------------------------------------------------------


class TestValidateStaff:
    def test_valid(self):
        assert validate_staff("山田", 480, 5, 3) == []

    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_name_invalid(self, name):
        errors = validate_staff(name, 480, 5, 3)
        codes = [e.code for e in errors]
        assert STAFF_NAME_REQUIRED in codes

    def test_name_stripped_still_valid(self):
        assert validate_staff("  山田  ", 480, 5, 3) == []

    @pytest.mark.parametrize("minutes", [1, 1440])
    def test_daily_work_minutes_boundary_valid(self, minutes):
        errors = validate_staff("山田", minutes, 5, 3)
        assert errors == []

    @pytest.mark.parametrize("minutes", [0, 1441, -1])
    def test_daily_work_minutes_boundary_invalid(self, minutes):
        errors = validate_staff("山田", minutes, 5, 3)
        assert STAFF_DAILY_WORK_MINUTES_INVALID in [e.code for e in errors]

    def test_daily_work_minutes_bool_rejected(self):
        errors = validate_staff("山田", True, 5, 3)
        assert STAFF_DAILY_WORK_MINUTES_INVALID in [e.code for e in errors]

    def test_daily_work_minutes_non_int_rejected(self):
        errors = validate_staff("山田", 480.5, 5, 3)
        assert STAFF_DAILY_WORK_MINUTES_INVALID in [e.code for e in errors]

    @pytest.mark.parametrize("days", [1, 100])
    def test_max_consecutive_days_valid(self, days):
        assert validate_staff("山田", 480, days, 3) == []

    @pytest.mark.parametrize("days", [0, -1])
    def test_max_consecutive_days_invalid(self, days):
        errors = validate_staff("山田", 480, days, 3)
        assert STAFF_MAX_CONSECUTIVE_DAYS_INVALID in [e.code for e in errors]

    def test_max_consecutive_days_bool_rejected(self):
        errors = validate_staff("山田", 480, False, 3)
        assert STAFF_MAX_CONSECUTIVE_DAYS_INVALID in [e.code for e in errors]

    @pytest.mark.parametrize("skill_level", [1, 2, 3, 4, 5])
    def test_skill_level_boundary_valid(self, skill_level):
        assert validate_staff("山田", 480, 5, skill_level) == []

    @pytest.mark.parametrize("skill_level", [0, 6])
    def test_skill_level_out_of_range_invalid(self, skill_level):
        errors = validate_staff("山田", 480, 5, skill_level)
        assert STAFF_SKILL_LEVEL_INVALID in [e.code for e in errors]
        assert any(e.message == "スキルは1〜5で入力してください。" for e in errors)

    @pytest.mark.parametrize("skill_level", [True, False, None, 3.5, "3"])
    def test_skill_level_invalid_type_rejected(self, skill_level):
        errors = validate_staff("山田", 480, 5, skill_level)
        assert STAFF_SKILL_LEVEL_INVALID in [e.code for e in errors]

    def test_multiple_errors_collected(self):
        errors = validate_staff("", 0, 0, 0)
        codes = {e.code for e in errors}
        assert codes == {
            STAFF_NAME_REQUIRED,
            STAFF_DAILY_WORK_MINUTES_INVALID,
            STAFF_MAX_CONSECUTIVE_DAYS_INVALID,
            STAFF_SKILL_LEVEL_INVALID,
        }


# ---------------------------------------------------------------------------
# T20 validate_monthly_condition
# ---------------------------------------------------------------------------


class TestValidateMonthlyCondition:
    def test_valid_minimal(self):
        cond = MonthlyConditionInput(staff_id=1, year_month="2026-10", target_monthly_minutes=9600)
        assert validate_monthly_condition(cond, max_consecutive_days=5) == []

    def test_invalid_year_month(self):
        cond = MonthlyConditionInput(staff_id=1, year_month="2026/10", target_monthly_minutes=9600)
        errors = validate_monthly_condition(cond, max_consecutive_days=5)
        assert any(e.field_name == "year_month" for e in errors)

    def test_min_equals_target_valid(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="2026-10",
            target_monthly_minutes=9600,
            min_monthly_minutes=9600,
        )
        assert validate_monthly_condition(cond, max_consecutive_days=5) == []

    def test_min_exceeds_target_pc10(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="2026-10",
            target_monthly_minutes=9600,
            min_monthly_minutes=9601,
        )
        errors = validate_monthly_condition(cond, max_consecutive_days=5)
        assert MONTHLY_CONDITION_MIN_EXCEEDS_TARGET in [e.code for e in errors]

    def test_target_equals_max_valid(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="2026-10",
            target_monthly_minutes=9600,
            max_monthly_minutes=9600,
        )
        assert validate_monthly_condition(cond, max_consecutive_days=5) == []

    def test_target_exceeds_max_pc11(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="2026-10",
            target_monthly_minutes=9601,
            max_monthly_minutes=9600,
        )
        errors = validate_monthly_condition(cond, max_consecutive_days=5)
        assert MONTHLY_CONDITION_TARGET_EXCEEDS_MAX in [e.code for e in errors]

    def test_min_exceeds_max(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="2026-10",
            target_monthly_minutes=9600,
            min_monthly_minutes=9700,
            max_monthly_minutes=9600,
        )
        errors = validate_monthly_condition(cond, max_consecutive_days=5)
        codes = [e.code for e in errors]
        assert MONTHLY_CONDITION_MIN_EXCEEDS_MAX in codes

    def test_min_max_none_skips_checks(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="2026-10",
            target_monthly_minutes=9600,
            min_monthly_minutes=None,
            max_monthly_minutes=None,
        )
        assert validate_monthly_condition(cond, max_consecutive_days=5) == []

    def test_carryover_zero_valid(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="2026-10",
            target_monthly_minutes=9600,
            carryover_consecutive_days=0,
        )
        assert validate_monthly_condition(cond, max_consecutive_days=5) == []

    def test_carryover_negative_pc12(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="2026-10",
            target_monthly_minutes=9600,
            carryover_consecutive_days=-1,
        )
        errors = validate_monthly_condition(cond, max_consecutive_days=5)
        assert MONTHLY_CONDITION_CARRYOVER_NEGATIVE in [e.code for e in errors]

    def test_carryover_equals_max_valid(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="2026-10",
            target_monthly_minutes=9600,
            carryover_consecutive_days=5,
        )
        assert validate_monthly_condition(cond, max_consecutive_days=5) == []

    def test_carryover_exceeds_max_pc13(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="2026-10",
            target_monthly_minutes=9600,
            carryover_consecutive_days=6,
        )
        errors = validate_monthly_condition(cond, max_consecutive_days=5)
        assert MONTHLY_CONDITION_CARRYOVER_EXCEEDS_MAX in [e.code for e in errors]

    def test_target_bool_rejected(self):
        cond = MonthlyConditionInput(staff_id=1, year_month="2026-10", target_monthly_minutes=True)
        errors = validate_monthly_condition(cond, max_consecutive_days=5)
        assert any(e.field_name == "target_monthly_minutes" for e in errors)

    def test_multiple_errors_collected(self):
        cond = MonthlyConditionInput(
            staff_id=1,
            year_month="bad",
            target_monthly_minutes=-1,
            min_monthly_minutes=-5,
            max_monthly_minutes=-5,
            carryover_consecutive_days=-1,
        )
        errors = validate_monthly_condition(cond, max_consecutive_days=5)
        assert len(errors) >= 5


# ---------------------------------------------------------------------------
# T21 validate_daily_requirement
# ---------------------------------------------------------------------------


class TestValidateDailyRequirement:
    def test_valid_no_roles(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5)
        assert validate_daily_requirement(req, []) == []

    def test_invalid_date(self):
        req = DailyRequirementInput(work_date="2026-13-01", required_total_staff=5)
        errors = validate_daily_requirement(req, [])
        assert any(e.field_name == "work_date" for e in errors)

    def test_required_equals_max_valid(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5, max_total_staff=5)
        assert validate_daily_requirement(req, []) == []

    def test_required_exceeds_max_pc08(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=6, max_total_staff=5)
        errors = validate_daily_requirement(req, [])
        assert REQUIREMENT_REQUIRED_EXCEEDS_MAX in [e.code for e in errors]

    def test_max_none_skips_pc08(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=100)
        assert validate_daily_requirement(req, []) == []

    def test_occupancy_rate_none_ok(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5, occupancy_rate=None)
        assert validate_daily_requirement(req, []) == []

    def test_occupancy_rate_negative_invalid(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5, occupancy_rate=-0.1)
        errors = validate_daily_requirement(req, [])
        assert any(e.field_name == "occupancy_rate" for e in errors)

    def test_role_sum_equals_required_valid(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5)
        roles = [
            RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=2),
            RoleRequirementInput(work_date="2026-10-01", role_id=2, required_count=3),
        ]
        assert validate_daily_requirement(req, roles) == []

    def test_role_sum_exceeds_required_pc07(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=4)
        roles = [
            RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=2),
            RoleRequirementInput(work_date="2026-10-01", role_id=2, required_count=3),
        ]
        errors = validate_daily_requirement(req, roles)
        assert REQUIREMENT_ROLE_SUM_EXCEEDS_REQUIRED in [e.code for e in errors]

    def test_role_sum_exceeds_max_pc09(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=10, max_total_staff=4)
        roles = [
            RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=2),
            RoleRequirementInput(work_date="2026-10-01", role_id=2, required_count=3),
        ]
        errors = validate_daily_requirement(req, roles)
        codes = [e.code for e in errors]
        assert REQUIREMENT_ROLE_SUM_EXCEEDS_MAX in codes
        assert REQUIREMENT_ROLE_SUM_EXCEEDS_REQUIRED not in codes

    def test_role_requirements_other_date_ignored(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=1)
        roles = [
            RoleRequirementInput(work_date="2026-10-02", role_id=1, required_count=100),
        ]
        assert validate_daily_requirement(req, roles) == []

    def test_role_required_count_negative_invalid(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5)
        roles = [RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=-1)]
        errors = validate_daily_requirement(req, roles)
        assert any(e.field_name == "required_count" for e in errors)

    def test_required_total_staff_bool_rejected(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=True)
        errors = validate_daily_requirement(req, [])
        assert any(e.field_name == "required_total_staff" for e in errors)


# ---------------------------------------------------------------------------
# T22 validate_preference
# ---------------------------------------------------------------------------


class TestValidatePreference:
    def test_valid_unavailable_any_day(self):
        staff = _staff(weekday_availability={w: False for w in range(7)})
        pref = PreferenceInput(staff_id=1, work_date="2026-10-01", preference_type="UNAVAILABLE")
        assert validate_preference(pref, staff) == []

    def test_valid_prefer_off_on_unavailable_weekday(self):
        staff = _staff(weekday_availability={w: False for w in range(7)})
        pref = PreferenceInput(staff_id=1, work_date="2026-10-01", preference_type="PREFER_OFF")
        assert validate_preference(pref, staff) == []

    def test_prefer_work_on_available_weekday_valid(self):
        staff = _staff(weekday_availability={w: True for w in range(7)})
        pref = PreferenceInput(staff_id=1, work_date="2026-10-01", preference_type="PREFER_WORK")
        assert validate_preference(pref, staff) == []

    def test_prefer_work_on_unavailable_weekday_invalid(self):
        # 2026-10-01 is Thursday (weekday index 3)
        staff = _staff(weekday_availability={0: True, 1: True, 2: True, 3: False, 4: True, 5: True, 6: True})
        pref = PreferenceInput(staff_id=1, work_date="2026-10-01", preference_type="PREFER_WORK")
        errors = validate_preference(pref, staff)
        assert PREFERENCE_PREFER_WORK_ON_UNAVAILABLE_WEEKDAY in [e.code for e in errors]

    def test_staff_id_mismatch(self):
        staff = _staff(staff_id=1)
        pref = PreferenceInput(staff_id=2, work_date="2026-10-01", preference_type="UNAVAILABLE")
        errors = validate_preference(pref, staff)
        assert PREFERENCE_STAFF_ID_MISMATCH in [e.code for e in errors]

    def test_invalid_date(self):
        staff = _staff()
        pref = PreferenceInput(staff_id=1, work_date="not-a-date", preference_type="UNAVAILABLE")
        errors = validate_preference(pref, staff)
        assert any(e.field_name == "work_date" for e in errors)
