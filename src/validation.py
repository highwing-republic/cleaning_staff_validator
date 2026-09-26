"""入力検証（§19-§23, §27, T19-T22）.

各関数は list[ValidationError] を返す（空 = 有効）。ユーザー入力に対して例外を送出しない。
"""

from datetime import date

from src.constants import PREFERENCE_PREFER_WORK, SKILL_LEVEL_MAX, SKILL_LEVEL_MIN
from src.models import (
    DailyRequirementInput,
    MonthlyConditionInput,
    PreferenceInput,
    RoleRequirementInput,
    StaffInput,
    ValidationError,
)
from src.month_utils import parse_year_month, weekday_index

# ---------------------------------------------------------------------------
# エラーコード（T19 Staff）
# ---------------------------------------------------------------------------

STAFF_NAME_REQUIRED = "STAFF_NAME_REQUIRED"
STAFF_DAILY_WORK_MINUTES_INVALID = "STAFF_DAILY_WORK_MINUTES_INVALID"
STAFF_MAX_CONSECUTIVE_DAYS_INVALID = "STAFF_MAX_CONSECUTIVE_DAYS_INVALID"
STAFF_SKILL_LEVEL_INVALID = "STAFF_SKILL_LEVEL_INVALID"

# エラーコード（T20 Monthly Condition）
MONTHLY_CONDITION_YEAR_MONTH_INVALID = "MONTHLY_CONDITION_YEAR_MONTH_INVALID"
MONTHLY_CONDITION_TARGET_INVALID = "MONTHLY_CONDITION_TARGET_INVALID"
MONTHLY_CONDITION_MIN_INVALID = "MONTHLY_CONDITION_MIN_INVALID"
MONTHLY_CONDITION_MAX_INVALID = "MONTHLY_CONDITION_MAX_INVALID"
MONTHLY_CONDITION_MIN_EXCEEDS_TARGET = "PC10"
MONTHLY_CONDITION_TARGET_EXCEEDS_MAX = "PC11"
MONTHLY_CONDITION_MIN_EXCEEDS_MAX = "MONTHLY_CONDITION_MIN_EXCEEDS_MAX"
MONTHLY_CONDITION_CARRYOVER_INVALID = "MONTHLY_CONDITION_CARRYOVER_INVALID"
MONTHLY_CONDITION_CARRYOVER_NEGATIVE = "PC12"
MONTHLY_CONDITION_CARRYOVER_EXCEEDS_MAX = "PC13"

# エラーコード（T21 Requirement）
REQUIREMENT_WORK_DATE_INVALID = "REQUIREMENT_WORK_DATE_INVALID"
REQUIREMENT_REQUIRED_TOTAL_STAFF_INVALID = "REQUIREMENT_REQUIRED_TOTAL_STAFF_INVALID"
REQUIREMENT_MAX_TOTAL_STAFF_INVALID = "REQUIREMENT_MAX_TOTAL_STAFF_INVALID"
REQUIREMENT_REQUIRED_EXCEEDS_MAX = "PC08"
REQUIREMENT_OCCUPANCY_RATE_INVALID = "REQUIREMENT_OCCUPANCY_RATE_INVALID"
REQUIREMENT_ROLE_REQUIRED_COUNT_INVALID = "REQUIREMENT_ROLE_REQUIRED_COUNT_INVALID"
REQUIREMENT_ROLE_SUM_EXCEEDS_REQUIRED = "PC07"
REQUIREMENT_ROLE_SUM_EXCEEDS_MAX = "PC09"

# エラーコード（T22 Preference）
PREFERENCE_WORK_DATE_INVALID = "PREFERENCE_WORK_DATE_INVALID"
PREFERENCE_STAFF_ID_MISMATCH = "PREFERENCE_STAFF_ID_MISMATCH"
PREFERENCE_PREFER_WORK_ON_UNAVAILABLE_WEEKDAY = "PREFERENCE_PREFER_WORK_ON_UNAVAILABLE_WEEKDAY"


def _is_valid_int(value: object) -> bool:
    """bool を除く int かどうか."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_valid_number(value: object) -> bool:
    """bool を除く int/float かどうか."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_valid_date(work_date: object) -> bool:
    if not isinstance(work_date, str):
        return False
    try:
        date.fromisoformat(work_date)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# T19 Staff Validation
# ---------------------------------------------------------------------------


def validate_staff(
    staff_name: str,
    daily_work_minutes: int,
    max_consecutive_days: int,
    skill_level: int,
) -> list[ValidationError]:
    """スタッフ入力の検証（§19, §13-§14）."""
    errors: list[ValidationError] = []

    if not isinstance(staff_name, str) or not staff_name.strip():
        errors.append(
            ValidationError(
                code=STAFF_NAME_REQUIRED,
                message="スタッフ名を入力してください。",
                field_name="staff_name",
            )
        )

    if not _is_valid_int(daily_work_minutes) or not 1 <= daily_work_minutes <= 1440:
        errors.append(
            ValidationError(
                code=STAFF_DAILY_WORK_MINUTES_INVALID,
                message="1日の勤務時間（分）は1〜1440の整数で入力してください。",
                field_name="daily_work_minutes",
            )
        )

    if not _is_valid_int(max_consecutive_days) or max_consecutive_days < 1:
        errors.append(
            ValidationError(
                code=STAFF_MAX_CONSECUTIVE_DAYS_INVALID,
                message="最大連続勤務日数は1以上の整数で入力してください。",
                field_name="max_consecutive_days",
            )
        )

    if (
        not _is_valid_int(skill_level)
        or not SKILL_LEVEL_MIN <= skill_level <= SKILL_LEVEL_MAX
    ):
        errors.append(
            ValidationError(
                code=STAFF_SKILL_LEVEL_INVALID,
                message="スキルは1〜5で入力してください。",
                field_name="skill_level",
            )
        )

    return errors


# ---------------------------------------------------------------------------
# T20 Monthly Condition Validation
# ---------------------------------------------------------------------------


def validate_monthly_condition(
    cond: MonthlyConditionInput,
    max_consecutive_days: int,
) -> list[ValidationError]:
    """月間勤務条件の検証（§21, PC10-PC13）."""
    errors: list[ValidationError] = []
    staff_id = cond.staff_id

    try:
        parse_year_month(cond.year_month)
    except ValueError:
        errors.append(
            ValidationError(
                code=MONTHLY_CONDITION_YEAR_MONTH_INVALID,
                message="年月は YYYY-MM 形式で入力してください。",
                staff_id=staff_id,
                field_name="year_month",
            )
        )

    target = cond.target_monthly_minutes
    target_valid = _is_valid_int(target) and target >= 0
    if not target_valid:
        errors.append(
            ValidationError(
                code=MONTHLY_CONDITION_TARGET_INVALID,
                message="所定勤務分は0以上の整数で入力してください。",
                staff_id=staff_id,
                field_name="target_monthly_minutes",
            )
        )

    min_minutes = cond.min_monthly_minutes
    min_valid = min_minutes is None or (_is_valid_int(min_minutes) and min_minutes >= 0)
    if not min_valid:
        errors.append(
            ValidationError(
                code=MONTHLY_CONDITION_MIN_INVALID,
                message="最低勤務分は0以上の整数で入力してください。",
                staff_id=staff_id,
                field_name="min_monthly_minutes",
            )
        )

    max_minutes = cond.max_monthly_minutes
    max_valid = max_minutes is None or (_is_valid_int(max_minutes) and max_minutes >= 0)
    if not max_valid:
        errors.append(
            ValidationError(
                code=MONTHLY_CONDITION_MAX_INVALID,
                message="最大勤務分は0以上の整数で入力してください。",
                staff_id=staff_id,
                field_name="max_monthly_minutes",
            )
        )

    # PC10: min > target
    if min_valid and target_valid and min_minutes is not None and min_minutes > target:
        errors.append(
            ValidationError(
                code=MONTHLY_CONDITION_MIN_EXCEEDS_TARGET,
                message="最低勤務分が所定勤務分を超えています。",
                staff_id=staff_id,
                field_name="min_monthly_minutes",
            )
        )

    # PC11: target > max
    if target_valid and max_valid and max_minutes is not None and target > max_minutes:
        errors.append(
            ValidationError(
                code=MONTHLY_CONDITION_TARGET_EXCEEDS_MAX,
                message="所定勤務分が最大勤務分を超えています。",
                staff_id=staff_id,
                field_name="target_monthly_minutes",
            )
        )

    # min <= max（両方設定時）
    if (
        min_valid
        and max_valid
        and min_minutes is not None
        and max_minutes is not None
        and min_minutes > max_minutes
    ):
        errors.append(
            ValidationError(
                code=MONTHLY_CONDITION_MIN_EXCEEDS_MAX,
                message="最低勤務分が最大勤務分を超えています。",
                staff_id=staff_id,
                field_name="min_monthly_minutes",
            )
        )

    carryover = cond.carryover_consecutive_days
    carryover_valid = _is_valid_int(carryover)
    if not carryover_valid:
        errors.append(
            ValidationError(
                code=MONTHLY_CONDITION_CARRYOVER_INVALID,
                message="前月からの連続勤務日数は整数で入力してください。",
                staff_id=staff_id,
                field_name="carryover_consecutive_days",
            )
        )
    else:
        # PC12: carryover < 0
        if carryover < 0:
            errors.append(
                ValidationError(
                    code=MONTHLY_CONDITION_CARRYOVER_NEGATIVE,
                    message="前月からの連続勤務日数は0以上で入力してください。",
                    staff_id=staff_id,
                    field_name="carryover_consecutive_days",
                )
            )
        # PC13: carryover > max_consecutive_days
        if _is_valid_int(max_consecutive_days) and carryover > max_consecutive_days:
            errors.append(
                ValidationError(
                    code=MONTHLY_CONDITION_CARRYOVER_EXCEEDS_MAX,
                    message="前月からの連続勤務日数が最大連続勤務日数を超えています。",
                    staff_id=staff_id,
                    field_name="carryover_consecutive_days",
                )
            )

    return errors


# ---------------------------------------------------------------------------
# T21 Daily Requirement Validation
# ---------------------------------------------------------------------------


def validate_daily_requirement(
    req: DailyRequirementInput,
    role_requirements: list[RoleRequirementInput],
) -> list[ValidationError]:
    """日別最低人数・ロール別必要人数の検証（§23, §24, PC07-PC09）."""
    errors: list[ValidationError] = []
    work_date = req.work_date

    if not _is_valid_date(work_date):
        errors.append(
            ValidationError(
                code=REQUIREMENT_WORK_DATE_INVALID,
                message="日付は YYYY-MM-DD 形式で入力してください。",
                work_date=work_date if isinstance(work_date, str) else None,
                field_name="work_date",
            )
        )

    required_total = req.required_total_staff
    required_valid = _is_valid_int(required_total) and required_total >= 0
    if not required_valid:
        errors.append(
            ValidationError(
                code=REQUIREMENT_REQUIRED_TOTAL_STAFF_INVALID,
                message="最低人数は0以上の整数で入力してください。",
                work_date=work_date,
                field_name="required_total_staff",
            )
        )

    max_total = req.max_total_staff
    max_valid = max_total is None or (_is_valid_int(max_total) and max_total >= 0)
    if not max_valid:
        errors.append(
            ValidationError(
                code=REQUIREMENT_MAX_TOTAL_STAFF_INVALID,
                message="最大人数は0以上の整数で入力してください。",
                work_date=work_date,
                field_name="max_total_staff",
            )
        )

    # PC08: required > max
    if required_valid and max_valid and max_total is not None and required_total > max_total:
        errors.append(
            ValidationError(
                code=REQUIREMENT_REQUIRED_EXCEEDS_MAX,
                message="最低人数が最大人数を超えています。",
                work_date=work_date,
                field_name="required_total_staff",
            )
        )

    occupancy_rate = req.occupancy_rate
    occupancy_valid = occupancy_rate is None or (
        _is_valid_number(occupancy_rate) and occupancy_rate >= 0
    )
    if not occupancy_valid:
        errors.append(
            ValidationError(
                code=REQUIREMENT_OCCUPANCY_RATE_INVALID,
                message="稼働率は0以上の数値で入力してください。",
                work_date=work_date,
                field_name="occupancy_rate",
            )
        )

    # 対象日のロール別必要人数のみ対象
    relevant_roles = [r for r in role_requirements if r.work_date == work_date]

    role_count_valid = True
    for role_req in relevant_roles:
        count = role_req.required_count
        if not _is_valid_int(count) or count < 0:
            role_count_valid = False
            errors.append(
                ValidationError(
                    code=REQUIREMENT_ROLE_REQUIRED_COUNT_INVALID,
                    message="ロール別必要人数は0以上の整数で入力してください。",
                    work_date=work_date,
                    role_id=role_req.role_id,
                    field_name="required_count",
                )
            )

    if role_count_valid:
        role_sum = sum(r.required_count for r in relevant_roles)

        # PC07: sum(role) > required_total_staff
        if required_valid and role_sum > required_total:
            errors.append(
                ValidationError(
                    code=REQUIREMENT_ROLE_SUM_EXCEEDS_REQUIRED,
                    message="ロール別必要人数の合計が最低人数を超えています。",
                    work_date=work_date,
                )
            )

        # PC09: sum(role) > max_total_staff
        if max_valid and max_total is not None and role_sum > max_total:
            errors.append(
                ValidationError(
                    code=REQUIREMENT_ROLE_SUM_EXCEEDS_MAX,
                    message="ロール別必要人数の合計が最大人数を超えています。",
                    work_date=work_date,
                )
            )

    return errors


# ---------------------------------------------------------------------------
# T22 Preference Validation
# ---------------------------------------------------------------------------


def validate_preference(pref: PreferenceInput, staff: StaffInput) -> list[ValidationError]:
    """希望休入力の検証（§12, §22）."""
    errors: list[ValidationError] = []

    if not _is_valid_date(pref.work_date):
        errors.append(
            ValidationError(
                code=PREFERENCE_WORK_DATE_INVALID,
                message="日付は YYYY-MM-DD 形式で入力してください。",
                staff_id=pref.staff_id,
                work_date=pref.work_date if isinstance(pref.work_date, str) else None,
                field_name="work_date",
            )
        )
        # 日付が不正な場合、曜日判定はできないためここで終了
        return errors

    if pref.staff_id != staff.staff_id:
        errors.append(
            ValidationError(
                code=PREFERENCE_STAFF_ID_MISMATCH,
                message="スタッフIDが一致しません。",
                staff_id=pref.staff_id,
                work_date=pref.work_date,
                field_name="staff_id",
            )
        )
        return errors

    if pref.preference_type == PREFERENCE_PREFER_WORK:
        weekday = weekday_index(pref.work_date)
        if not staff.is_available_on(weekday):
            errors.append(
                ValidationError(
                    code=PREFERENCE_PREFER_WORK_ON_UNAVAILABLE_WEEKDAY,
                    message="通常勤務不可曜日に「できれば勤務」は指定できません。",
                    staff_id=pref.staff_id,
                    work_date=pref.work_date,
                    field_name="preference_type",
                )
            )

    return errors
