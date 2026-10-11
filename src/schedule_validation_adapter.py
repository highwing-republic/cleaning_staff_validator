"""保存済み勤務表（schedule_assignments）の検証アダプタ.

検証対象は勤怠CSV（attendance_shifts）ではなく、アプリで作成・手修正した
現在の勤務表である。両者はDB上でも混ぜない。

判定ロジックは Phase 3〜4 の staffing_validation をそのまま再利用する。
そのため勤務表の1セルを検証エンジンの入力（1人1日の勤務セル）へ変換する。
勤務表のセルはスタッフマスターと必ず紐づき、勤務時刻も確定しているため、
勤怠CSVで起きる「勤務区分不明」「マスター未登録」は発生しない。
結果として WARNING（可能性あり）の判定は出ず、充足/不足が確定値で決まる。

このモジュールはDBアクセスを行わない。
"""

import dataclasses

from src.constants import (
    CLEANING_DEPARTMENT,
    SCHEDULE_DAY_DRAFT,
    SHIFT_TYPE_TIME_RANGE,
    VALIDATION_STATUS_INFO,
    VALIDATION_STATUS_WARNING,
)
from src.models import (
    AttendanceShiftInput,
    CurrentScheduleView,
    DailyRequirementInput,
    RoleRequirementInput,
    ScheduleAssignmentRecord,
    ScheduleDayValidationResult,
    SchedulePeriodValidationResult,
    ScheduleDayView,
    StaffInput,
    ValidationIssue,
)
from src.navigation import PAGE_GENERATE, PAGE_SCHEDULE
from src.staffing_validation import (
    REQUIREMENT_MISSING,
    ROLE_COMBINATION_SHORTAGE,
    ROLE_SHORTAGE,
    SKILL_SHORTAGE,
    STAFF_OVER_MAX,
    STAFF_SHORTAGE,
    validate_day,
)
from src.work_time import parse_hhmm

# ---------------------------------------------------------------------------
# Issue コード（Phase 11固有. 体制の判定コードは staffing_validation と共有する）
# ---------------------------------------------------------------------------

# 勤務表そのものが未作成（人数の検証以前の状態）
SCHEDULE_MISSING = "SCHEDULE_MISSING"
# 勤務表はあるが、有効スタッフ分の行が欠けている（防御的な検出）
SCHEDULE_INCOMPLETE = "SCHEDULE_INCOMPLETE"
# 勤務表が下書きのまま（問題ではないが、印刷・共有時に明示する）
SCHEDULE_DRAFT = "SCHEDULE_DRAFT"


def _to_shift_input(
    assignment: ScheduleAssignmentRecord, staff: StaffInput
) -> AttendanceShiftInput:
    """勤務表の出勤セルを検証エンジンの入力へ変換する.

    勤務表の出勤は清掃勤務が確定しているため available_for_cleaning=True とする。
    """
    return AttendanceShiftInput(
        employee_code=staff.employee_code,
        work_date=assignment.work_date,
        raw_shift=f"{assignment.start_time}-{assignment.end_time}",
        shift_type=SHIFT_TYPE_TIME_RANGE,
        available_for_cleaning=True,
        staff_id=staff.staff_id,
        employee_name=staff.staff_name,
        department=staff.department or CLEANING_DEPARTMENT,
        start_minutes=parse_hhmm(assignment.start_time),
        end_minutes=parse_hhmm(assignment.end_time),
    )


def to_shift_inputs(
    day: ScheduleDayView, staff_by_id: dict[int, StaffInput]
) -> list[AttendanceShiftInput]:
    """その日の出勤セルだけを検証エンジンの入力へ変換する（休みは人数に含めない）."""
    return [
        _to_shift_input(assignment, staff_by_id[staff_id])
        for staff_id, assignment in sorted(day.assignments.items())
        if assignment.is_working and staff_id in staff_by_id
    ]


def _schedule_issues(
    day: ScheduleDayView | None,
    work_date: str,
    missing_assignment_count: int,
) -> list[ValidationIssue]:
    if day is None or not day.exists:
        return [
            ValidationIssue(
                SCHEDULE_MISSING,
                VALIDATION_STATUS_WARNING,
                f"この日の勤務表が未作成です（「{PAGE_GENERATE}」で作成してください）。",
                work_date,
            )
        ]

    issues: list[ValidationIssue] = []
    if missing_assignment_count:
        issues.append(
            ValidationIssue(
                SCHEDULE_INCOMPLETE,
                VALIDATION_STATUS_WARNING,
                f"勤務表に行がない有効スタッフが{missing_assignment_count}名います"
                "（下書きの日は「固定を守って再生成」で揃います。"
                "確定日は確定を解除してから再生成してください）。",
                work_date,
                actual=missing_assignment_count,
            )
        )
    if day.day is not None and day.day.status == SCHEDULE_DAY_DRAFT:
        issues.append(
            ValidationIssue(
                SCHEDULE_DRAFT,
                VALIDATION_STATUS_INFO,
                f"この日の勤務表は下書きです（「{PAGE_SCHEDULE}」で確定できます）。",
                work_date,
            )
        )
    return issues


def _rewrite_message(
    issue: ValidationIssue,
    role_names: dict[int, str],
    requirement: DailyRequirementInput | None,
) -> ValidationIssue:
    """判定は変えず、勤務表向けの言い方に直す.

    共通エンジンのメッセージは勤怠CSV前提で「勤務区分不明」「未登録」に触れるが、
    勤務表ではどちらも起こらないため、若女将が読んで分かる文に置き換える。
    code・severity・人数はそのまま保つ（attendance検証との一致を崩さないため）。
    """
    required = issue.required
    actual = issue.actual
    if issue.code == REQUIREMENT_MISSING:
        message = (
            "この日の必要条件が未設定のため、人数・ロール・スキルを判定できません"
            "（清掃不要日は必要人数0を登録してください）。"
        )
    elif required is None or actual is None:
        return issue
    elif issue.code == STAFF_SHORTAGE:
        message = (
            f"清掃スタッフが{required - actual}名不足しています"
            f"（必要：{required}名／勤務予定：{actual}名）。"
        )
    elif issue.code == STAFF_OVER_MAX:
        message = (
            f"清掃スタッフが上限を{actual - required}名超えています"
            f"（上限：{required}名／勤務予定：{actual}名）。"
        )
    elif issue.code == ROLE_SHORTAGE:
        label = role_names.get(issue.role_id, "対象ロール")
        message = (
            f"{label}が{required - actual}名不足しています"
            f"（必要：{required}名／勤務予定：{actual}名）。"
        )
    elif issue.code == SKILL_SHORTAGE:
        level = requirement.required_skill_level if requirement else None
        label = f"スキル{level}以上の清掃スタッフ" if level else "スキル条件を満たす清掃スタッフ"
        message = (
            f"{label}が{required - actual}名不足しています"
            f"（必要：{required}名／勤務予定：{actual}名）。"
        )
    elif issue.code == ROLE_COMBINATION_SHORTAGE:
        message = (
            "必要なロールを同時に満たせません"
            "（1人が複数のロールを兼ねることはできないため、勤務予定者が足りていません）。"
        )
    else:
        return issue
    return dataclasses.replace(issue, message=message)


def validate_schedule_day(
    work_date: str,
    day: ScheduleDayView | None,
    requirement: DailyRequirementInput | None,
    role_requirements: dict[int, int],
    staff_by_id: dict[int, StaffInput],
    role_names: dict[int, str],
    active_staff_ids: set[int] | frozenset[int] = frozenset(),
) -> ScheduleDayValidationResult:
    """勤務表1日分を検証する.

    勤務表が未作成の日は体制の判定を行わない（SCHEDULE_MISSING のみ）。
    locked や手修正（source=MANUAL）のセルも通常どおり判定対象にする。
    """
    exists = day is not None and day.exists
    missing = (
        len([sid for sid in active_staff_ids if sid not in day.assignments])
        if exists
        else 0
    )
    schedule_issues = _schedule_issues(day, work_date, missing)

    if not exists:
        return ScheduleDayValidationResult(
            work_date=work_date,
            schedule_status=None,
            staffing=None,
            requirement=requirement,
            scheduled_staff=0,
            missing_assignment_count=0,
            schedule_issues=schedule_issues,
        )

    shifts = to_shift_inputs(day, staff_by_id)
    staffing = validate_day(
        work_date, shifts, requirement, role_requirements, staff_by_id, role_names
    )
    staffing = dataclasses.replace(
        staffing,
        issues=[_rewrite_message(i, role_names, requirement) for i in staffing.issues],
    )
    return ScheduleDayValidationResult(
        work_date=work_date,
        schedule_status=day.day.status,
        staffing=staffing,
        requirement=requirement,
        scheduled_staff=len(shifts),
        missing_assignment_count=missing,
        schedule_issues=schedule_issues,
    )


def validate_schedule_period(
    work_dates: list[str],
    schedule: CurrentScheduleView,
    daily_requirements: list[DailyRequirementInput],
    role_requirements: list[RoleRequirementInput],
    staff: list[StaffInput],
    role_names: dict[int, str],
    active_staff_ids: set[int] | frozenset[int] | None = None,
) -> SchedulePeriodValidationResult:
    """対象期間の勤務表を検証する（勤務表がない日も1日として返す）.

    staff には無効スタッフも含めること（過去に勤務表へ入っている可能性があるため）。
    active_staff_ids を省略すると staff の active から求める。
    """
    staff_by_id = {s.staff_id: s for s in staff}
    if active_staff_ids is None:
        active_staff_ids = frozenset(s.staff_id for s in staff if s.active)

    requirement_by_date = {r.work_date: r for r in daily_requirements}
    roles_by_date: dict[str, dict[int, int]] = {}
    for r in role_requirements:
        roles_by_date.setdefault(r.work_date, {})[r.role_id] = r.required_count

    days = [
        validate_schedule_day(
            work_date,
            schedule.day(work_date),
            requirement_by_date.get(work_date),
            roles_by_date.get(work_date, {}),
            staff_by_id,
            role_names,
            active_staff_ids,
        )
        for work_date in work_dates
    ]
    return SchedulePeriodValidationResult(work_dates=list(work_dates), days=days)
