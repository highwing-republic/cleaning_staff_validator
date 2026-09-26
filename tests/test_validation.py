import pytest

from src import validation
from src.models import DailyRequirementInput, RoleRequirementInput
from src.validation import (
    REQUIREMENT_REQUIRED_EXCEEDS_MAX,
    REQUIREMENT_ROLE_SUM_EXCEEDS_MAX,
    REQUIREMENT_SKILL_COUNT_INVALID,
    REQUIREMENT_SKILL_LEVEL_INVALID,
    REQUIREMENT_SKILL_LEVEL_REQUIRED,
    STAFF_EMPLOYEE_CODE_REQUIRED,
    STAFF_NAME_REQUIRED,
    STAFF_SKILL_LEVEL_INVALID,
    validate_daily_requirement,
    validate_staff,
)


def _codes(errors):
    return [e.code for e in errors]


# ---------------------------------------------------------------------------
# validate_staff
# ---------------------------------------------------------------------------


class TestValidateStaff:
    def test_valid(self):
        assert validate_staff("0015", "山田", 3) == []

    @pytest.mark.parametrize("code", ["", "   ", None, 15])
    def test_employee_code_invalid(self, code):
        # 数値型は受け付けない（先頭0を失わないよう文字列で扱う）
        assert STAFF_EMPLOYEE_CODE_REQUIRED in _codes(validate_staff(code, "山田", 3))

    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_name_invalid(self, name):
        assert STAFF_NAME_REQUIRED in _codes(validate_staff("1", name, 3))

    def test_name_stripped_still_valid(self):
        assert validate_staff(" 1 ", "  山田  ", 3) == []

    @pytest.mark.parametrize("skill_level", [1, 2, 3, 4, 5])
    def test_skill_level_boundary_valid(self, skill_level):
        assert validate_staff("1", "山田", skill_level) == []

    @pytest.mark.parametrize("skill_level", [0, 6])
    def test_skill_level_out_of_range_invalid(self, skill_level):
        errors = validate_staff("1", "山田", skill_level)
        assert STAFF_SKILL_LEVEL_INVALID in _codes(errors)
        assert any(e.message == "スキルは1〜5で入力してください。" for e in errors)

    @pytest.mark.parametrize("skill_level", [True, False, None, 3.5, "3"])
    def test_skill_level_invalid_type_rejected(self, skill_level):
        assert STAFF_SKILL_LEVEL_INVALID in _codes(validate_staff("1", "山田", skill_level))

    def test_multiple_errors_collected(self):
        assert set(_codes(validate_staff("", "", 0))) == {
            STAFF_EMPLOYEE_CODE_REQUIRED,
            STAFF_NAME_REQUIRED,
            STAFF_SKILL_LEVEL_INVALID,
        }


def test_generation_validators_removed():
    for name in ("validate_monthly_condition", "validate_preference"):
        assert not hasattr(validation, name), name
    assert not hasattr(validation, "REQUIREMENT_ROLE_SUM_EXCEEDS_REQUIRED")


# ---------------------------------------------------------------------------
# validate_daily_requirement
# ---------------------------------------------------------------------------


def _roles(*counts, work_date="2026-10-01"):
    return [
        RoleRequirementInput(work_date=work_date, role_id=i, required_count=c)
        for i, c in enumerate(counts, start=1)
    ]


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

    def test_required_exceeds_max(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=6, max_total_staff=5)
        assert REQUIREMENT_REQUIRED_EXCEEDS_MAX in _codes(validate_daily_requirement(req, []))

    def test_max_none_skips_required_exceeds_max(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=100)
        assert validate_daily_requirement(req, []) == []

    def test_occupancy_rate_none_ok(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5, occupancy_rate=None)
        assert validate_daily_requirement(req, []) == []

    def test_occupancy_rate_negative_invalid(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5, occupancy_rate=-0.1)
        errors = validate_daily_requirement(req, [])
        assert any(e.field_name == "occupancy_rate" for e in errors)

    def test_role_sum_exceeding_minimum_staff_is_valid(self):
        """最低5名・LEADER3・CHECKER3 → 6名以上で成立するため入力エラーではない."""
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5)
        assert validate_daily_requirement(req, _roles(3, 3)) == []

    def test_role_sum_exceeding_minimum_with_room_in_max_is_valid(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5, max_total_staff=6)
        assert validate_daily_requirement(req, _roles(3, 3)) == []

    def test_role_sum_exceeds_max(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=4, max_total_staff=4)
        assert _codes(validate_daily_requirement(req, _roles(2, 3))) == [
            REQUIREMENT_ROLE_SUM_EXCEEDS_MAX
        ]

    def test_role_sum_without_max_never_exceeds(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=1)
        assert validate_daily_requirement(req, _roles(10, 10, 10)) == []

    def test_role_requirements_other_date_ignored(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=1, max_total_staff=1)
        assert validate_daily_requirement(req, _roles(100, work_date="2026-10-02")) == []

    def test_role_required_count_negative_invalid(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5)
        errors = validate_daily_requirement(req, _roles(-1))
        assert any(e.field_name == "required_count" for e in errors)

    def test_required_total_staff_bool_rejected(self):
        req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=True)
        errors = validate_daily_requirement(req, [])
        assert any(e.field_name == "required_total_staff" for e in errors)


class TestValidateSkillRequirement:
    @pytest.mark.parametrize("level,count", [(4, 2), (None, 0), (1, 0), (5, 1)])
    def test_valid(self, level, count):
        req = DailyRequirementInput(
            work_date="2026-10-01", required_total_staff=5,
            required_skill_level=level, required_skill_count=count,
        )
        assert validate_daily_requirement(req, []) == []

    @pytest.mark.parametrize("level", [0, 6, True, 3.5])
    def test_level_invalid(self, level):
        req = DailyRequirementInput(
            work_date="2026-10-01", required_total_staff=5,
            required_skill_level=level, required_skill_count=1,
        )
        assert REQUIREMENT_SKILL_LEVEL_INVALID in _codes(validate_daily_requirement(req, []))

    @pytest.mark.parametrize("count", [-1, None, 1.5, True])
    def test_count_invalid(self, count):
        req = DailyRequirementInput(
            work_date="2026-10-01", required_total_staff=5,
            required_skill_level=4, required_skill_count=count,
        )
        assert REQUIREMENT_SKILL_COUNT_INVALID in _codes(validate_daily_requirement(req, []))

    def test_count_without_level(self):
        req = DailyRequirementInput(
            work_date="2026-10-01", required_total_staff=5, required_skill_count=2,
        )
        assert _codes(validate_daily_requirement(req, [])) == [REQUIREMENT_SKILL_LEVEL_REQUIRED]
