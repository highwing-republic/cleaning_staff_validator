"""シフト生成前の事前チェック（§42 PC01〜PC19）.

Solver実行前に、原因を特定できる不成立条件を検出する。
データ不足（PC01〜PC04）がある場合は、以降のチェックの前提が崩れるためそこで打ち切る。
"""

from collections import defaultdict

from src.availability import build_availability_map, count_available_staff
from src.constants import PREFERENCE_UNAVAILABLE
from src.models import (
    MonthlyConditionInput,
    PrecheckResult,
    SchedulerInput,
    StaffInput,
    ValidationError,
)
from src.month_utils import get_month_dates, weekday_index

ALL_WEEKDAYS = frozenset(range(7))


def run_precheck(
    scheduler_input: SchedulerInput,
    role_names: dict[int, str] | None = None,
) -> PrecheckResult:
    """事前チェックを実行する. role_names はメッセージ表示用（role_id -> 表示名）."""
    role_names = role_names or {}

    errors = check_missing_data(scheduler_input)
    if errors:
        return PrecheckResult(errors=errors)

    errors += check_daily_staffing(scheduler_input)
    errors += check_role_staffing(scheduler_input, role_names)
    errors += check_input_conflicts(scheduler_input)
    errors += check_fixed_hours(scheduler_input)
    errors += check_fixed_consecutive(scheduler_input)
    return PrecheckResult(errors=errors)


# ---------------------------------------------------------------------------
# T24 PC01〜PC04 データ不足
# ---------------------------------------------------------------------------


def check_missing_data(scheduler_input: SchedulerInput) -> list[ValidationError]:
    errors: list[ValidationError] = []
    active_staff = _active_staff(scheduler_input)

    # PC01
    if not active_staff:
        errors.append(ValidationError("PC01", "有効なスタッフが登録されていません。"))

    # PC02
    requirement_dates = {r.work_date for r in scheduler_input.daily_requirements}
    for work_date in get_month_dates(scheduler_input.year_month):
        if work_date not in requirement_dates:
            errors.append(
                ValidationError(
                    "PC02",
                    f"{_format_date(work_date)}の最低人数が入力されていません。",
                    work_date=work_date,
                )
            )

    # PC03
    condition_staff_ids = {
        c.staff_id
        for c in scheduler_input.monthly_conditions
        if c.year_month == scheduler_input.year_month
    }
    for staff in active_staff:
        if staff.staff_id not in condition_staff_ids:
            errors.append(
                ValidationError(
                    "PC03",
                    f"{staff.staff_name}さんの月間勤務条件が入力されていません。",
                    staff_id=staff.staff_id,
                )
            )

    # PC04
    for staff in active_staff:
        if set(staff.weekday_availability) != ALL_WEEKDAYS:
            errors.append(
                ValidationError(
                    "PC04",
                    f"{staff.staff_name}さんの通常勤務可能曜日データが不足しています。",
                    staff_id=staff.staff_id,
                )
            )

    return errors


# ---------------------------------------------------------------------------
# T25 PC05 最低人数 > 勤務可能人数
# ---------------------------------------------------------------------------


def check_daily_staffing(scheduler_input: SchedulerInput) -> list[ValidationError]:
    errors: list[ValidationError] = []
    availability_map = build_availability_map(scheduler_input)

    for req in _requirements_in_month(scheduler_input):
        available = count_available_staff(availability_map, req.work_date)
        if req.required_total_staff > available:
            shortage = req.required_total_staff - available
            errors.append(
                ValidationError(
                    "PC05",
                    f"{_format_date(req.work_date)}\n"
                    f"最低人数{req.required_total_staff}\n"
                    f"勤務可能{available}\n\n"
                    f"{shortage}名不足しています。",
                    work_date=req.work_date,
                )
            )
    return errors


# ---------------------------------------------------------------------------
# T26 PC06 必要ロール > ロール別勤務可能人数
# ---------------------------------------------------------------------------


def check_role_staffing(
    scheduler_input: SchedulerInput,
    role_names: dict[int, str] | None = None,
) -> list[ValidationError]:
    role_names = role_names or {}
    errors: list[ValidationError] = []
    availability_map = build_availability_map(scheduler_input)
    month_dates = set(get_month_dates(scheduler_input.year_month))

    for role_req in scheduler_input.role_requirements:
        if role_req.work_date not in month_dates or role_req.required_count <= 0:
            continue
        available = count_available_staff(
            availability_map,
            role_req.work_date,
            role_id=role_req.role_id,
            staff=scheduler_input.staff,
        )
        if role_req.required_count > available:
            shortage = role_req.required_count - available
            role_label = _role_label(role_req.role_id, role_names)
            errors.append(
                ValidationError(
                    "PC06",
                    f"{_format_date(role_req.work_date)}\n"
                    f"{role_label} 必要{role_req.required_count}\n"
                    f"勤務可能{available}\n\n"
                    f"{shortage}名不足しています。",
                    work_date=role_req.work_date,
                    role_id=role_req.role_id,
                )
            )
    return errors


# ---------------------------------------------------------------------------
# T27 PC07〜PC17 入力矛盾
# ---------------------------------------------------------------------------


def check_input_conflicts(scheduler_input: SchedulerInput) -> list[ValidationError]:
    errors: list[ValidationError] = []
    errors += _check_requirement_conflicts(scheduler_input)
    errors += _check_condition_conflicts(scheduler_input)
    errors += _check_lock_conflicts(scheduler_input)
    return errors


def _check_requirement_conflicts(scheduler_input: SchedulerInput) -> list[ValidationError]:
    """PC07〜PC09."""
    errors: list[ValidationError] = []
    role_sum: dict[str, int] = defaultdict(int)
    for role_req in scheduler_input.role_requirements:
        role_sum[role_req.work_date] += role_req.required_count

    for req in _requirements_in_month(scheduler_input):
        label = _format_date(req.work_date)
        total_roles = role_sum.get(req.work_date, 0)
        if total_roles > req.required_total_staff:
            errors.append(
                ValidationError(
                    "PC07",
                    f"{label}：ロール別必要人数の合計({total_roles})が"
                    f"最低人数({req.required_total_staff})を超えています。",
                    work_date=req.work_date,
                )
            )
        if req.max_total_staff is None:
            continue
        if req.required_total_staff > req.max_total_staff:
            errors.append(
                ValidationError(
                    "PC08",
                    f"{label}：最低人数({req.required_total_staff})が"
                    f"最大人数({req.max_total_staff})を超えています。",
                    work_date=req.work_date,
                )
            )
        if total_roles > req.max_total_staff:
            errors.append(
                ValidationError(
                    "PC09",
                    f"{label}：ロール別必要人数の合計({total_roles})が"
                    f"最大人数({req.max_total_staff})を超えています。",
                    work_date=req.work_date,
                )
            )
    return errors


def _check_condition_conflicts(scheduler_input: SchedulerInput) -> list[ValidationError]:
    """PC10〜PC13."""
    errors: list[ValidationError] = []
    conditions = _conditions_by_staff(scheduler_input)

    for staff in _active_staff(scheduler_input):
        cond = conditions.get(staff.staff_id)
        if cond is None:
            continue
        name = staff.staff_name
        if cond.min_monthly_minutes is not None and cond.min_monthly_minutes > cond.target_monthly_minutes:
            errors.append(
                ValidationError(
                    "PC10",
                    f"{name}さん：最低勤務時間が所定勤務時間を超えています。",
                    staff_id=staff.staff_id,
                    field_name="min_monthly_minutes",
                )
            )
        if cond.max_monthly_minutes is not None and cond.target_monthly_minutes > cond.max_monthly_minutes:
            errors.append(
                ValidationError(
                    "PC11",
                    f"{name}さん：所定勤務時間が最大勤務時間を超えています。",
                    staff_id=staff.staff_id,
                    field_name="max_monthly_minutes",
                )
            )
        if cond.carryover_consecutive_days < 0:
            errors.append(
                ValidationError(
                    "PC12",
                    f"{name}さん：前月からの連勤日数が負の値です。",
                    staff_id=staff.staff_id,
                    field_name="carryover_consecutive_days",
                )
            )
        if cond.carryover_consecutive_days > staff.max_consecutive_days:
            errors.append(
                ValidationError(
                    "PC13",
                    f"{name}さん：前月からの連勤日数({cond.carryover_consecutive_days})が"
                    f"最大連勤日数({staff.max_consecutive_days})を超えています。",
                    staff_id=staff.staff_id,
                    field_name="carryover_consecutive_days",
                )
            )
    return errors


def _check_lock_conflicts(scheduler_input: SchedulerInput) -> list[ValidationError]:
    """PC14〜PC17（固定出勤との矛盾）."""
    errors: list[ValidationError] = []
    staff_by_id = {s.staff_id: s for s in _active_staff(scheduler_input)}
    month_dates = set(get_month_dates(scheduler_input.year_month))
    unavailable = {
        (p.staff_id, p.work_date)
        for p in scheduler_input.preferences
        if p.preference_type == PREFERENCE_UNAVAILABLE
    }
    requirements = {r.work_date: r for r in _requirements_in_month(scheduler_input)}
    locked_work_count: dict[str, int] = defaultdict(int)

    for lock in scheduler_input.locked_assignments:
        staff = staff_by_id.get(lock.staff_id)
        if not lock.is_working or staff is None or lock.work_date not in month_dates:
            continue
        locked_work_count[lock.work_date] += 1
        label = f"{_format_date(lock.work_date)} {staff.staff_name}さん"

        # PC14
        if (lock.staff_id, lock.work_date) in unavailable:
            errors.append(
                ValidationError(
                    "PC14",
                    f"{label}：固定出勤ですが絶対休みが指定されています。",
                    staff_id=lock.staff_id,
                    work_date=lock.work_date,
                )
            )
        # PC15
        if not staff.is_available_on(weekday_index(lock.work_date)):
            errors.append(
                ValidationError(
                    "PC15",
                    f"{label}：固定出勤ですが通常勤務不可の曜日です。",
                    staff_id=lock.staff_id,
                    work_date=lock.work_date,
                )
            )
        # PC16
        req = requirements.get(lock.work_date)
        if req is not None and req.required_total_staff == 0:
            errors.append(
                ValidationError(
                    "PC16",
                    f"{label}：最低人数0の日に固定出勤があります。",
                    staff_id=lock.staff_id,
                    work_date=lock.work_date,
                )
            )

    # PC17
    for work_date in sorted(locked_work_count):
        req = requirements.get(work_date)
        if req is None or req.max_total_staff is None:
            continue
        if locked_work_count[work_date] > req.max_total_staff:
            errors.append(
                ValidationError(
                    "PC17",
                    f"{_format_date(work_date)}：固定出勤人数({locked_work_count[work_date]})が"
                    f"最大人数({req.max_total_staff})を超えています。",
                    work_date=work_date,
                )
            )
    return errors


# ---------------------------------------------------------------------------
# T28 PC18 固定だけで最大勤務時間超過
# ---------------------------------------------------------------------------


def check_fixed_hours(scheduler_input: SchedulerInput) -> list[ValidationError]:
    errors: list[ValidationError] = []
    conditions = _conditions_by_staff(scheduler_input)
    locked_days = _locked_work_dates_by_staff(scheduler_input)

    for staff in _active_staff(scheduler_input):
        cond = conditions.get(staff.staff_id)
        if cond is None or cond.max_monthly_minutes is None:
            continue
        fixed_minutes = len(locked_days.get(staff.staff_id, ())) * staff.daily_work_minutes
        if fixed_minutes > cond.max_monthly_minutes:
            errors.append(
                ValidationError(
                    "PC18",
                    f"{staff.staff_name}さん：固定出勤だけで{fixed_minutes}分となり、"
                    f"最大勤務時間({cond.max_monthly_minutes}分)を超えています。",
                    staff_id=staff.staff_id,
                )
            )
    return errors


# ---------------------------------------------------------------------------
# T29 PC19 固定だけで最大連勤超過（前月連勤を含む）
# ---------------------------------------------------------------------------


def check_fixed_consecutive(scheduler_input: SchedulerInput) -> list[ValidationError]:
    errors: list[ValidationError] = []
    conditions = _conditions_by_staff(scheduler_input)
    locked_days = _locked_work_dates_by_staff(scheduler_input)
    month_dates = get_month_dates(scheduler_input.year_month)

    for staff in _active_staff(scheduler_input):
        fixed = locked_days.get(staff.staff_id)
        if not fixed:
            continue
        cond = conditions.get(staff.staff_id)
        carryover = cond.carryover_consecutive_days if cond is not None else 0
        limit = staff.max_consecutive_days

        # 月初の連続は前月からの連勤を引き継ぐ（HC10）
        run = max(carryover, 0)
        run_start = month_dates[0]
        reported = False
        for work_date in month_dates:
            if work_date in fixed:
                if run == 0:
                    run_start = work_date
                run += 1
                if run > limit and not reported:
                    errors.append(
                        ValidationError(
                            "PC19",
                            f"{staff.staff_name}さん：{_format_date(run_start)}からの固定出勤が"
                            f"最大連勤日数({limit}日)を超えています。",
                            staff_id=staff.staff_id,
                            work_date=work_date,
                        )
                    )
                    reported = True
            else:
                run = 0
                reported = False
    return errors


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _active_staff(scheduler_input: SchedulerInput) -> list[StaffInput]:
    return [s for s in scheduler_input.staff if s.active]


def _requirements_in_month(scheduler_input: SchedulerInput):
    month_dates = set(get_month_dates(scheduler_input.year_month))
    return sorted(
        (r for r in scheduler_input.daily_requirements if r.work_date in month_dates),
        key=lambda r: r.work_date,
    )


def _conditions_by_staff(scheduler_input: SchedulerInput) -> dict[int, MonthlyConditionInput]:
    return {
        c.staff_id: c
        for c in scheduler_input.monthly_conditions
        if c.year_month == scheduler_input.year_month
    }


def _locked_work_dates_by_staff(scheduler_input: SchedulerInput) -> dict[int, set[str]]:
    month_dates = set(get_month_dates(scheduler_input.year_month))
    result: dict[int, set[str]] = defaultdict(set)
    for lock in scheduler_input.locked_assignments:
        if lock.is_working and lock.work_date in month_dates:
            result[lock.staff_id].add(lock.work_date)
    return result


def _format_date(work_date: str) -> str:
    _, month, day = work_date.split("-")
    return f"{int(month)}月{int(day)}日"


def _role_label(role_id: int, role_names: dict[int, str]) -> str:
    return role_names.get(role_id, f"ロール{role_id}")
