"""手動変更後のシフト検証（§44〜45, §29 HC01〜HC10, Phase 11 T57〜T59）.

管理者が手動編集した月間シフトを、全Hard Constraintに対して再検証する。
Hard違反が1件でもあれば確定不可（§45）。Lock自体はここでは検証しない
（Lockは管理者自身の入力のため）。
"""

from src.constants import PREFERENCE_UNAVAILABLE
from src.models import (
    AssignmentResult,
    DailyRequirementInput,
    MonthlyConditionInput,
    SchedulerInput,
    ValidationError,
)
from src.month_utils import get_month_dates, weekday_index


def validate_schedule(
    scheduler_input: SchedulerInput,
    assignments: list[AssignmentResult],
    role_names: dict[int, str] | None = None,
) -> list[ValidationError]:
    """シフト全体をHard Constraintに対して検証する（§45）."""
    errors: list[ValidationError] = []
    errors += check_weekday(scheduler_input, assignments)
    errors += check_unavailable(scheduler_input, assignments)
    errors += check_staffing(scheduler_input, assignments)
    errors += check_max_staff(scheduler_input, assignments)
    errors += check_roles(scheduler_input, assignments, role_names)
    errors += check_hours(scheduler_input, assignments)
    errors += check_consecutive(scheduler_input, assignments)
    return errors


# ---------------------------------------------------------------------------
# T57 HC01/HC02/HC03/HC04/HC05/HC06
# ---------------------------------------------------------------------------


def check_weekday(
    scheduler_input: SchedulerInput,
    assignments: list[AssignmentResult],
) -> list[ValidationError]:
    """HC01: 通常勤務不可曜日に出勤していないか."""
    working_cells = _working_cells(scheduler_input, assignments)
    staff_by_id = _active_staff_by_id(scheduler_input)
    errors: list[ValidationError] = []
    for staff_id, work_date in working_cells:
        staff = staff_by_id[staff_id]
        if not staff.is_available_on(weekday_index(work_date)):
            errors.append(
                ValidationError(
                    "HC01",
                    f"{_format_date(work_date)}：{staff.staff_name}さんは"
                    f"通常勤務不可の曜日に出勤しています。",
                    staff_id=staff_id,
                    work_date=work_date,
                )
            )
    return _sort_errors(errors)


def check_unavailable(
    scheduler_input: SchedulerInput,
    assignments: list[AssignmentResult],
) -> list[ValidationError]:
    """HC02: 絶対休み(UNAVAILABLE)の日に出勤していないか."""
    working_cells = _working_cells(scheduler_input, assignments)
    staff_by_id = _active_staff_by_id(scheduler_input)
    unavailable = {
        (p.staff_id, p.work_date)
        for p in scheduler_input.preferences
        if p.preference_type == PREFERENCE_UNAVAILABLE
    }
    errors: list[ValidationError] = []
    for staff_id, work_date in working_cells:
        if (staff_id, work_date) not in unavailable:
            continue
        staff = staff_by_id[staff_id]
        errors.append(
            ValidationError(
                "HC02",
                f"{_format_date(work_date)}：{staff.staff_name}さんは"
                f"絶対休みの日に出勤しています。",
                staff_id=staff_id,
                work_date=work_date,
            )
        )
    return _sort_errors(errors)


def check_staffing(
    scheduler_input: SchedulerInput,
    assignments: list[AssignmentResult],
) -> list[ValidationError]:
    """HC03: 最低人数未満 / HC04: 最低人数0の日に出勤者がいる."""
    working_cells = _working_cells(scheduler_input, assignments)
    active_ids = {s.staff_id for s in scheduler_input.staff if s.active}
    errors: list[ValidationError] = []

    for req in _requirements_in_month(scheduler_input):
        actual = _count_working(active_ids, working_cells, req.work_date)
        if req.required_total_staff == 0:
            if actual > 0:
                errors.append(
                    ValidationError(
                        "HC04",
                        f"{_format_date(req.work_date)}：最低人数0の日に"
                        f"{actual}名が出勤しています。",
                        work_date=req.work_date,
                    )
                )
        elif actual < req.required_total_staff:
            shortage = req.required_total_staff - actual
            errors.append(
                ValidationError(
                    "HC03",
                    f"{_format_date(req.work_date)}：最低人数{req.required_total_staff}"
                    f"に対して出勤{actual}（{shortage}名不足）",
                    work_date=req.work_date,
                )
            )
    return _sort_errors(errors)


def check_max_staff(
    scheduler_input: SchedulerInput,
    assignments: list[AssignmentResult],
) -> list[ValidationError]:
    """HC05: max_total_staffを超えていないか（T57）."""
    working_cells = _working_cells(scheduler_input, assignments)
    active_ids = {s.staff_id for s in scheduler_input.staff if s.active}
    errors: list[ValidationError] = []

    for req in _requirements_in_month(scheduler_input):
        if req.max_total_staff is None:
            continue
        actual = _count_working(active_ids, working_cells, req.work_date)
        if actual > req.max_total_staff:
            over = actual - req.max_total_staff
            errors.append(
                ValidationError(
                    "HC05",
                    f"{_format_date(req.work_date)}：最大人数{req.max_total_staff}"
                    f"に対して出勤{actual}（{over}名超過）",
                    work_date=req.work_date,
                )
            )
    return _sort_errors(errors)


def check_roles(
    scheduler_input: SchedulerInput,
    assignments: list[AssignmentResult],
    role_names: dict[int, str] | None = None,
) -> list[ValidationError]:
    """HC06: ロール別必要人数未満（行がなければrequired 0扱い）."""
    role_names = role_names or {}
    working_cells = _working_cells(scheduler_input, assignments)
    role_by_staff_id = {
        s.staff_id: s.role_id for s in scheduler_input.staff if s.active
    }
    month_dates_with_requirement = {
        r.work_date for r in _requirements_in_month(scheduler_input)
    }
    errors: list[ValidationError] = []

    for role_req in scheduler_input.role_requirements:
        if role_req.work_date not in month_dates_with_requirement:
            continue
        if role_req.required_count <= 0:
            continue
        actual = sum(
            1
            for staff_id, role_id in role_by_staff_id.items()
            if role_id == role_req.role_id
            and (staff_id, role_req.work_date) in working_cells
        )
        if actual < role_req.required_count:
            shortage = role_req.required_count - actual
            label = _role_label(role_req.role_id, role_names)
            errors.append(
                ValidationError(
                    "HC06",
                    f"{_format_date(role_req.work_date)}：{label}必要人数"
                    f"{role_req.required_count}に対して出勤{actual}"
                    f"（{shortage}名不足）",
                    work_date=role_req.work_date,
                    role_id=role_req.role_id,
                )
            )
    return _sort_errors(errors)


# ---------------------------------------------------------------------------
# T58 HC07/HC08
# ---------------------------------------------------------------------------


def check_hours(
    scheduler_input: SchedulerInput,
    assignments: list[AssignmentResult],
) -> list[ValidationError]:
    """HC07: 最大月間勤務時間超過 / HC08: 最低月間勤務時間未達."""
    working_cells = _working_cells(scheduler_input, assignments)
    conditions = _conditions_by_staff(scheduler_input)
    month_dates = get_month_dates(scheduler_input.year_month)
    errors: list[ValidationError] = []

    for staff in _active_staff(scheduler_input):
        cond = conditions.get(staff.staff_id)
        if cond is None:
            continue
        actual = sum(
            staff.daily_work_minutes
            for work_date in month_dates
            if (staff.staff_id, work_date) in working_cells
        )
        if cond.max_monthly_minutes is not None and actual > cond.max_monthly_minutes:
            errors.append(
                ValidationError(
                    "HC07",
                    f"{staff.staff_name}さん：実績{actual}分が"
                    f"最大勤務時間({cond.max_monthly_minutes}分)を超えています。",
                    staff_id=staff.staff_id,
                )
            )
        if cond.min_monthly_minutes is not None and actual < cond.min_monthly_minutes:
            errors.append(
                ValidationError(
                    "HC08",
                    f"{staff.staff_name}さん：実績{actual}分が"
                    f"最低勤務時間({cond.min_monthly_minutes}分)を下回っています。",
                    staff_id=staff.staff_id,
                )
            )
    return _sort_errors(errors)


def summarize_staff_minutes(
    scheduler_input: SchedulerInput,
    assignments: list[AssignmentResult],
) -> dict[int, int]:
    """活動中スタッフごとの実績勤務分を集計する（UI表示用）."""
    working_cells = _working_cells(scheduler_input, assignments)
    month_dates = get_month_dates(scheduler_input.year_month)
    result: dict[int, int] = {}
    for staff in _active_staff(scheduler_input):
        result[staff.staff_id] = sum(
            staff.daily_work_minutes
            for work_date in month_dates
            if (staff.staff_id, work_date) in working_cells
        )
    return result


# ---------------------------------------------------------------------------
# T59 HC09/HC10
# ---------------------------------------------------------------------------


def check_consecutive(
    scheduler_input: SchedulerInput,
    assignments: list[AssignmentResult],
) -> list[ValidationError]:
    """HC09: 月内の最大連勤超過 / HC10: 前月からの連勤(carryover)を含む超過.

    連続勤務の判定順（§29 HC09/HC10）。carryoverは月初の勤務日に継続する
    場合のみ引き継がれる（1日目が休みなら前月分の連勤はそこで途切れる）。
    1つの連勤ランにつき違反は1件のみ報告する（precheck PC19と同様）。
    """
    working_cells = _working_cells(scheduler_input, assignments)
    conditions = _conditions_by_staff(scheduler_input)
    month_dates = get_month_dates(scheduler_input.year_month)
    errors: list[ValidationError] = []

    for staff in _active_staff(scheduler_input):
        cond = conditions.get(staff.staff_id)
        carryover = cond.carryover_consecutive_days if cond is not None else 0
        limit = staff.max_consecutive_days

        run = max(carryover, 0)
        is_carryover_run = carryover > 0
        reported = False
        for work_date in month_dates:
            if (staff.staff_id, work_date) in working_cells:
                if run == 0:
                    is_carryover_run = False
                run += 1
                if run > limit and not reported:
                    if is_carryover_run:
                        errors.append(
                            ValidationError(
                                "HC10",
                                f"{staff.staff_name}さん：前月からの連勤"
                                f"(carryover{carryover}日)を含む連続勤務が"
                                f"{_format_date(work_date)}時点で"
                                f"最大連勤日数({limit}日)を超えています。",
                                staff_id=staff.staff_id,
                                work_date=work_date,
                            )
                        )
                    else:
                        errors.append(
                            ValidationError(
                                "HC09",
                                f"{staff.staff_name}さん：{_format_date(work_date)}"
                                f"までの連続勤務が最大連勤日数({limit}日)を"
                                f"超えています。",
                                staff_id=staff.staff_id,
                                work_date=work_date,
                            )
                        )
                    reported = True
            else:
                run = 0
                is_carryover_run = False
                reported = False
    return _sort_errors(errors)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _active_staff(scheduler_input: SchedulerInput):
    return sorted(
        (s for s in scheduler_input.staff if s.active),
        key=lambda s: s.staff_id,
    )


def _active_staff_by_id(scheduler_input: SchedulerInput) -> dict:
    return {s.staff_id: s for s in scheduler_input.staff if s.active}


def _requirements_in_month(
    scheduler_input: SchedulerInput,
) -> list[DailyRequirementInput]:
    month_dates = set(get_month_dates(scheduler_input.year_month))
    return sorted(
        (r for r in scheduler_input.daily_requirements if r.work_date in month_dates),
        key=lambda r: r.work_date,
    )


def _conditions_by_staff(
    scheduler_input: SchedulerInput,
) -> dict[int, MonthlyConditionInput]:
    return {
        c.staff_id: c
        for c in scheduler_input.monthly_conditions
        if c.year_month == scheduler_input.year_month
    }


def _working_cells(
    scheduler_input: SchedulerInput,
    assignments: list[AssignmentResult],
) -> set[tuple[int, str]]:
    """有効スタッフ・対象月内に限定した「出勤」セル一覧.

    未入力のセルは休みとみなす。同一(staff, date)に複数のAssignmentResultが
    与えられた場合は、リスト内の後の要素を優先する。
    """
    active_ids = {s.staff_id for s in scheduler_input.staff if s.active}
    month_dates = set(get_month_dates(scheduler_input.year_month))
    state: dict[tuple[int, str], bool] = {}
    for assignment in assignments:
        if assignment.staff_id not in active_ids:
            continue
        if assignment.work_date not in month_dates:
            continue
        state[(assignment.staff_id, assignment.work_date)] = assignment.is_working
    return {key for key, is_working in state.items() if is_working}


def _count_working(
    active_ids: set,
    working_cells: set[tuple[int, str]],
    work_date: str,
) -> int:
    return sum(1 for staff_id in active_ids if (staff_id, work_date) in working_cells)


def _sort_errors(errors: list[ValidationError]) -> list[ValidationError]:
    return sorted(errors, key=lambda e: (e.work_date or "", e.staff_id or 0))


def _format_date(work_date: str) -> str:
    _, month, day = work_date.split("-")
    return f"{int(month)}月{int(day)}日"


def _role_label(role_id: int, role_names: dict[int, str]) -> str:
    return role_names.get(role_id, f"ロール{role_id}")
