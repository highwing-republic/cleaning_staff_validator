import pytest

from src import validation
from src.models import DailyRequirementInput, RoleRequirementInput
from src.validation import (
    REQUIREMENT_REQUIRED_EXCEEDS_MAX,
    REQUIREMENT_RESERVED_ROOMS_INVALID,
    REQUIREMENT_ROLE_SUM_EXCEEDS_MAX,
    REQUIREMENT_SKILL_COUNT_INVALID,
    REQUIREMENT_SKILL_LEVEL_INVALID,
    REQUIREMENT_SKILL_LEVEL_REQUIRED,
    STAFF_EMPLOYEE_CODE_REQUIRED,
    STAFF_MAX_CONSECUTIVE_DAYS_INVALID,
    STAFF_MAX_DAYS_PER_PERIOD_INVALID,
    STAFF_NAME_REQUIRED,
    STAFF_SKILL_LEVEL_INVALID,
    STAFF_SPECIAL_SKILL_DUPLICATED,
    STAFF_STANDARD_TIME_INCOMPLETE,
    STAFF_STANDARD_TIME_INVALID,
    STAFF_STANDARD_TIME_ORDER_INVALID,
    STAFF_TARGET_DAYS_PER_WEEK_INVALID,
    STAFF_WEEKDAY_DUPLICATED,
    STAFF_WEEKDAY_INVALID,
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


# ---------------------------------------------------------------------------
# validate_staff: 通常勤務条件（Phase 5）
# ---------------------------------------------------------------------------


def _staff_codes(**kwargs):
    return _codes(validate_staff("0001", "山田", 3, **kwargs))


class TestValidateStandardWorkTime:
    @pytest.mark.parametrize(
        ("start", "end"),
        [(None, None), ("", ""), ("   ", None), (None, "  ")],
    )
    def test_unset_is_allowed(self, start, end):
        """通常勤務時間は任意項目（登録直後で未設定の場合があるため）."""
        assert _staff_codes(standard_start_time=start, standard_end_time=end) == []

    @pytest.mark.parametrize(
        ("start", "end"),
        [("09:00", "15:30"), ("9:00", "13:00"), ("00:00", "23:59")],
    )
    def test_valid_pairs(self, start, end):
        assert _staff_codes(standard_start_time=start, standard_end_time=end) == []

    def test_start_only_is_error(self):
        assert STAFF_STANDARD_TIME_INCOMPLETE in _staff_codes(standard_start_time="09:00")

    def test_end_only_is_error(self):
        assert STAFF_STANDARD_TIME_INCOMPLETE in _staff_codes(standard_end_time="15:30")

    @pytest.mark.parametrize(("start", "end"), [("09:00", "09:00"), ("15:00", "09:00")])
    def test_end_must_be_after_start(self, start, end):
        codes = _staff_codes(standard_start_time=start, standard_end_time=end)
        assert STAFF_STANDARD_TIME_ORDER_INVALID in codes

    @pytest.mark.parametrize("value", ["24:00", "30:00", "09:60", "0900", "9時"])
    def test_invalid_format_is_error(self, value):
        """勤怠CSVの翌日跨ぎ（30:00等）は通常勤務マスターでは受け付けない."""
        codes = _staff_codes(standard_start_time=value, standard_end_time="15:30")
        assert STAFF_STANDARD_TIME_INVALID in codes

    def test_order_not_reported_when_format_invalid(self):
        codes = _staff_codes(standard_start_time="9時", standard_end_time="15:30")
        assert STAFF_STANDARD_TIME_ORDER_INVALID not in codes


class TestValidateWorkVolume:
    @pytest.mark.parametrize("value", [1, 3, 4, 5, 7])
    def test_target_days_valid(self, value):
        assert _staff_codes(target_days_per_week=value) == []

    @pytest.mark.parametrize("value", [0, 8, -1, 3.5, "4", True])
    def test_target_days_invalid(self, value):
        assert STAFF_TARGET_DAYS_PER_WEEK_INVALID in _staff_codes(target_days_per_week=value)

    def test_target_days_unset_is_allowed(self):
        assert _staff_codes(target_days_per_week=None) == []

    @pytest.mark.parametrize("value", [0, -1, 2.5, "3"])
    def test_max_days_per_period_invalid(self, value):
        assert STAFF_MAX_DAYS_PER_PERIOD_INVALID in _staff_codes(max_days_per_period=value)

    @pytest.mark.parametrize("value", [1, 10, 14, 30])
    def test_max_days_per_period_valid(self, value):
        assert _staff_codes(max_days_per_period=value) == []

    @pytest.mark.parametrize("value", [0, -1, 1.5])
    def test_max_consecutive_days_invalid(self, value):
        assert STAFF_MAX_CONSECUTIVE_DAYS_INVALID in _staff_codes(max_consecutive_days=value)

    @pytest.mark.parametrize("value", [1, 5, 6, None])
    def test_max_consecutive_days_valid(self, value):
        assert _staff_codes(max_consecutive_days=value) == []


class TestValidateWeekdays:
    @pytest.mark.parametrize("weekdays", [None, [], [0], [0, 1, 3, 4, 5], [0, 1, 2, 3, 4, 5, 6]])
    def test_valid(self, weekdays):
        """曜日なしも許容する（新規登録直後で勤務条件未設定の場合があるため）."""
        assert _staff_codes(weekdays=weekdays) == []

    @pytest.mark.parametrize("weekdays", [[-1], [7], [0, 10], ["月"], [None], [True]])
    def test_out_of_range_is_error(self, weekdays):
        assert STAFF_WEEKDAY_INVALID in _staff_codes(weekdays=weekdays)

    def test_duplicate_is_error(self):
        assert STAFF_WEEKDAY_DUPLICATED in _staff_codes(weekdays=[0, 1, 1])

    def test_duplicate_reported_once(self):
        codes = _staff_codes(weekdays=[0, 0, 1, 1])
        assert codes.count(STAFF_WEEKDAY_DUPLICATED) == 1


class TestValidateSpecialSkillIds:
    @pytest.mark.parametrize("ids", [None, [], [1], [1, 2]])
    def test_valid(self, ids):
        assert _staff_codes(special_skill_ids=ids) == []

    def test_duplicate_is_error(self):
        assert STAFF_SPECIAL_SKILL_DUPLICATED in _staff_codes(special_skill_ids=[1, 1])


def test_standard_conditions_do_not_affect_base_validation():
    """通常勤務条件を渡しても既存の必須項目チェックは変わらない."""
    codes = set(
        _codes(
            validate_staff(
                "", "", 0,
                standard_start_time="09:00",
                standard_end_time="15:30",
                target_days_per_week=4,
            )
        )
    )
    assert codes == {
        STAFF_EMPLOYEE_CODE_REQUIRED,
        STAFF_NAME_REQUIRED,
        STAFF_SKILL_LEVEL_INVALID,
    }


# ---------------------------------------------------------------------------
# validate_daily_requirement: 予約室数（Phase 7）
# ---------------------------------------------------------------------------


class TestValidateReservedRooms:
    def _codes(self, reserved_rooms, required_total_staff=4):
        req = DailyRequirementInput(
            work_date="2026-10-20",
            required_total_staff=required_total_staff,
            reserved_rooms=reserved_rooms,
        )
        return _codes(validate_daily_requirement(req, []))

    @pytest.mark.parametrize("value", [None, 0, 1, 10, 999])
    def test_valid(self, value):
        """NULL（未入力）と0（予約室数0）はどちらも有効."""
        assert self._codes(value) == []

    @pytest.mark.parametrize("value", [-1, -10, 2.5, "8", True, False])
    def test_invalid(self, value):
        assert REQUIREMENT_RESERVED_ROOMS_INVALID in self._codes(value)

    @pytest.mark.parametrize(
        ("reserved_rooms", "required_total_staff"),
        [(8, 4), (0, 3), (20, 1), (0, 0), (None, 3)],
    )
    def test_no_consistency_check_against_required_staff(
        self, reserved_rooms, required_total_staff
    ):
        """アプリは必要人数を推定しないため、予約室数との整合性は検証しない."""
        assert self._codes(reserved_rooms, required_total_staff) == []

