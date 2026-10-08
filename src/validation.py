"""入力検証（スタッフ・日別必要条件）.

各関数は list[ValidationError] を返す（空 = 有効）。ユーザー入力に対して例外を送出しない。
"""

from datetime import date

from src.constants import (
    SKILL_LEVEL_MAX,
    SKILL_LEVEL_MIN,
    TARGET_DAYS_PER_WEEK_MAX,
    TARGET_DAYS_PER_WEEK_MIN,
    WEEKDAY_MAX,
    WEEKDAY_MIN,
)
from src.models import (
    DailyRequirementInput,
    RoleRequirementInput,
    ValidationError,
)
from src.work_time import parse_hhmm

# ---------------------------------------------------------------------------
# エラーコード（Staff）
# ---------------------------------------------------------------------------

STAFF_EMPLOYEE_CODE_REQUIRED = "STAFF_EMPLOYEE_CODE_REQUIRED"
STAFF_NAME_REQUIRED = "STAFF_NAME_REQUIRED"
STAFF_SKILL_LEVEL_INVALID = "STAFF_SKILL_LEVEL_INVALID"
# 通常勤務条件（Phase 5）
STAFF_STANDARD_TIME_INVALID = "STAFF_STANDARD_TIME_INVALID"
STAFF_STANDARD_TIME_INCOMPLETE = "STAFF_STANDARD_TIME_INCOMPLETE"
STAFF_STANDARD_TIME_ORDER_INVALID = "STAFF_STANDARD_TIME_ORDER_INVALID"
STAFF_TARGET_DAYS_PER_WEEK_INVALID = "STAFF_TARGET_DAYS_PER_WEEK_INVALID"
STAFF_MAX_DAYS_PER_PERIOD_INVALID = "STAFF_MAX_DAYS_PER_PERIOD_INVALID"
STAFF_MAX_CONSECUTIVE_DAYS_INVALID = "STAFF_MAX_CONSECUTIVE_DAYS_INVALID"
STAFF_WEEKDAY_INVALID = "STAFF_WEEKDAY_INVALID"
STAFF_WEEKDAY_DUPLICATED = "STAFF_WEEKDAY_DUPLICATED"
STAFF_SPECIAL_SKILL_DUPLICATED = "STAFF_SPECIAL_SKILL_DUPLICATED"
STAFF_SPECIAL_SKILL_NOT_FOUND = "STAFF_SPECIAL_SKILL_NOT_FOUND"

# エラーコード（Requirement）
REQUIREMENT_WORK_DATE_INVALID = "REQUIREMENT_WORK_DATE_INVALID"
REQUIREMENT_REQUIRED_TOTAL_STAFF_INVALID = "REQUIREMENT_REQUIRED_TOTAL_STAFF_INVALID"
REQUIREMENT_MAX_TOTAL_STAFF_INVALID = "REQUIREMENT_MAX_TOTAL_STAFF_INVALID"
REQUIREMENT_REQUIRED_EXCEEDS_MAX = "REQUIREMENT_REQUIRED_EXCEEDS_MAX"
REQUIREMENT_OCCUPANCY_RATE_INVALID = "REQUIREMENT_OCCUPANCY_RATE_INVALID"
REQUIREMENT_ROLE_REQUIRED_COUNT_INVALID = "REQUIREMENT_ROLE_REQUIRED_COUNT_INVALID"
REQUIREMENT_ROLE_SUM_EXCEEDS_MAX = "REQUIREMENT_ROLE_SUM_EXCEEDS_MAX"
REQUIREMENT_SKILL_LEVEL_INVALID = "REQUIREMENT_SKILL_LEVEL_INVALID"
REQUIREMENT_SKILL_COUNT_INVALID = "REQUIREMENT_SKILL_COUNT_INVALID"
REQUIREMENT_SKILL_LEVEL_REQUIRED = "REQUIREMENT_SKILL_LEVEL_REQUIRED"


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


def _is_non_blank_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


# ---------------------------------------------------------------------------
# Staff Validation
# ---------------------------------------------------------------------------


def validate_staff(
    employee_code: str,
    staff_name: str,
    skill_level: int,
    *,
    standard_start_time: str | None = None,
    standard_end_time: str | None = None,
    target_days_per_week: int | None = None,
    max_days_per_period: int | None = None,
    max_consecutive_days: int | None = None,
    weekdays: list[int] | tuple[int, ...] | None = None,
    special_skill_ids: list[int] | tuple[int, ...] | None = None,
) -> list[ValidationError]:
    """スタッフ入力の検証.

    employee_code は文字列で保持する（数値型は受け付けない。先頭0等を保つため）。
    通常勤務条件（standard_* 以降）は任意項目で、未入力（None・空文字）はエラーにしない。
    特殊スキルIDの存在確認はDBが必要なため services 側で行う。
    """
    errors: list[ValidationError] = []

    if not _is_non_blank_str(employee_code):
        errors.append(
            ValidationError(
                code=STAFF_EMPLOYEE_CODE_REQUIRED,
                message="従業員番号を入力してください。",
                field_name="employee_code",
            )
        )

    if not _is_non_blank_str(staff_name):
        errors.append(
            ValidationError(
                code=STAFF_NAME_REQUIRED,
                message="スタッフ名を入力してください。",
                field_name="staff_name",
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

    errors += _validate_standard_work_time(standard_start_time, standard_end_time)
    errors += _validate_optional_positive_int(
        target_days_per_week,
        code=STAFF_TARGET_DAYS_PER_WEEK_INVALID,
        field_name="target_days_per_week",
        message=(
            f"目標勤務日数/週は{TARGET_DAYS_PER_WEEK_MIN}〜{TARGET_DAYS_PER_WEEK_MAX}"
            "で入力してください。"
        ),
        minimum=TARGET_DAYS_PER_WEEK_MIN,
        maximum=TARGET_DAYS_PER_WEEK_MAX,
    )
    errors += _validate_optional_positive_int(
        max_days_per_period,
        code=STAFF_MAX_DAYS_PER_PERIOD_INVALID,
        field_name="max_days_per_period",
        message="期間内最大勤務日数は1以上の整数で入力してください。",
    )
    errors += _validate_optional_positive_int(
        max_consecutive_days,
        code=STAFF_MAX_CONSECUTIVE_DAYS_INVALID,
        field_name="max_consecutive_days",
        message="最大連続勤務日数は1以上の整数で入力してください。",
    )
    errors += _validate_weekdays(weekdays)
    errors += _validate_special_skill_ids(special_skill_ids)

    return errors


def _is_blank(value: object) -> bool:
    """未入力（None または空白のみの文字列）か."""
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _validate_standard_work_time(
    start_time: object, end_time: object
) -> list[ValidationError]:
    """通常勤務時刻の検証.

    両方未入力なら条件なし（エラーにしない）。片方だけの入力はエラー
    （勤務可能時間を計算できず、Solverで扱えないため）。
    """
    errors: list[ValidationError] = []
    start_blank, end_blank = _is_blank(start_time), _is_blank(end_time)

    if start_blank and end_blank:
        return errors

    if start_blank or end_blank:
        errors.append(
            ValidationError(
                code=STAFF_STANDARD_TIME_INCOMPLETE,
                message="通常勤務時間は開始・終了の両方を入力してください。",
                field_name="standard_end_time" if end_blank else "standard_start_time",
            )
        )

    start = None if start_blank else parse_hhmm(start_time)
    end = None if end_blank else parse_hhmm(end_time)

    for value, blank, field_name in (
        (start, start_blank, "standard_start_time"),
        (end, end_blank, "standard_end_time"),
    ):
        if not blank and value is None:
            errors.append(
                ValidationError(
                    code=STAFF_STANDARD_TIME_INVALID,
                    message="通常勤務時間は HH:MM（00:00〜23:59）で入力してください。",
                    field_name=field_name,
                )
            )

    if start is not None and end is not None and end <= start:
        errors.append(
            ValidationError(
                code=STAFF_STANDARD_TIME_ORDER_INVALID,
                message="通常勤務の終了時刻は開始時刻より後にしてください。",
                field_name="standard_end_time",
            )
        )

    return errors


def _validate_optional_positive_int(
    value: object,
    *,
    code: str,
    field_name: str,
    message: str,
    minimum: int = 1,
    maximum: int | None = None,
) -> list[ValidationError]:
    """任意の正整数項目の検証. 未入力（None・空文字）はエラーにしない."""
    if _is_blank(value):
        return []
    if not _is_valid_int(value) or value < minimum or (maximum is not None and value > maximum):
        return [ValidationError(code=code, message=message, field_name=field_name)]
    return []


def _validate_weekdays(
    weekdays: list[int] | tuple[int, ...] | None,
) -> list[ValidationError]:
    """通常勤務曜日の検証. 曜日なし（空）も許容する（登録直後で未設定の場合があるため）."""
    if weekdays is None:
        return []

    errors: list[ValidationError] = []
    seen: set[int] = set()
    duplicated = False
    for weekday in weekdays:
        if not _is_valid_int(weekday) or not WEEKDAY_MIN <= weekday <= WEEKDAY_MAX:
            errors.append(
                ValidationError(
                    code=STAFF_WEEKDAY_INVALID,
                    message="通常勤務曜日の指定が不正です。",
                    field_name="weekdays",
                )
            )
            continue
        if weekday in seen and not duplicated:
            duplicated = True
            errors.append(
                ValidationError(
                    code=STAFF_WEEKDAY_DUPLICATED,
                    message="通常勤務曜日が重複しています。",
                    field_name="weekdays",
                )
            )
        seen.add(weekday)
    return errors


def _validate_special_skill_ids(
    special_skill_ids: list[int] | tuple[int, ...] | None,
) -> list[ValidationError]:
    """特殊スキルIDの重複検証（存在確認は services 側で行う）."""
    if special_skill_ids is None:
        return []
    if len(set(special_skill_ids)) != len(list(special_skill_ids)):
        return [
            ValidationError(
                code=STAFF_SPECIAL_SKILL_DUPLICATED,
                message="同じ特殊スキルが重複して指定されています。",
                field_name="special_skill_ids",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Daily Requirement Validation
# ---------------------------------------------------------------------------


def validate_daily_requirement(
    req: DailyRequirementInput,
    role_requirements: list[RoleRequirementInput],
) -> list[ValidationError]:
    """日別必要条件（最低人数・最大人数・ロール別・スキル）の検証.

    required_total_staff は最低必要人数であり、ロール別必要人数の合計が
    これを超えても入力エラーにしない（例: 最低5名・LEADER3・CHECKER3 → 6名以上で成立）。
    """
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

    # required > max
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

    # sum(role) > max_total_staff（max未設定なら判定しない）
    if role_count_valid and max_valid and max_total is not None:
        role_sum = sum(r.required_count for r in relevant_roles)
        if role_sum > max_total:
            errors.append(
                ValidationError(
                    code=REQUIREMENT_ROLE_SUM_EXCEEDS_MAX,
                    message="ロール別必要人数の合計が最大人数を超えています。",
                    work_date=work_date,
                )
            )

    errors += _validate_skill_requirement(req)
    return errors


def _validate_skill_requirement(req: DailyRequirementInput) -> list[ValidationError]:
    """スキル条件: required_skill_count > 0 のときは required_skill_level(1〜5) が必須."""
    errors: list[ValidationError] = []
    work_date = req.work_date

    level = req.required_skill_level
    level_valid = level is None or (
        _is_valid_int(level) and SKILL_LEVEL_MIN <= level <= SKILL_LEVEL_MAX
    )
    if not level_valid:
        errors.append(
            ValidationError(
                code=REQUIREMENT_SKILL_LEVEL_INVALID,
                message="必要スキルレベルは1〜5で入力してください。",
                work_date=work_date,
                field_name="required_skill_level",
            )
        )

    count = req.required_skill_count
    count_valid = _is_valid_int(count) and count >= 0
    if not count_valid:
        errors.append(
            ValidationError(
                code=REQUIREMENT_SKILL_COUNT_INVALID,
                message="必要スキル人数は0以上の整数で入力してください。",
                work_date=work_date,
                field_name="required_skill_count",
            )
        )

    if count_valid and count > 0 and level is None:
        errors.append(
            ValidationError(
                code=REQUIREMENT_SKILL_LEVEL_REQUIRED,
                message="必要スキル人数を指定する場合は必要スキルレベルを入力してください。",
                work_date=work_date,
                field_name="required_skill_level",
            )
        )

    return errors
