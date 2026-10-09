"""日別清掃体制の検証エンジン.

DBアクセスは行わない。ACTIVE取込の勤務セル・日別必要条件・staff master を受け取り、
日ごとに人数・上限・ロール・スキル・データ品質を判定する。

人数の数え方（1日・1人1セル）:
- 確定清掃勤務者: available_for_cleaning = True（CSV部門=清掃 かつ TIME_RANGE）。未登録でも数える
- 不確実: CSV部門=清掃 かつ UNKNOWN。清掃勤務かもしれないが判断できない人
- OTHER_DUTY / BLANK / 清掃以外の部門は数えない

判定は「確定値で充足 → OK」「UNKNOWN・未登録が全員条件を満たせば充足 → WARNING」
「それでも不足 → ERROR」の3段階。ロール・スキルは staff master 照合済みの人だけを確定値とし、
未登録スタッフはどのロール・スキルでもあり得る人として最大可能人数に含める。
ただし1人1ロールのため、未登録スタッフを複数ロールへ二重に割り当てられない。
ロール別判定の後に全ロールを同時に満たせるかを判定する（ROLE_COMBINATION_SHORTAGE）。
"""

from dataclasses import dataclass

from src.attendance_import import is_cleaning_department
from src.constants import (
    SHIFT_TYPE_UNKNOWN,
    VALIDATION_STATUS_ERROR,
    VALIDATION_STATUS_INFO,
    VALIDATION_STATUS_OK,
    VALIDATION_STATUS_WARNING,
)
from src.models import (
    AttendanceShiftInput,
    DailyRequirementInput,
    DailyStaffingResult,
    RoleRequirementInput,
    StaffInput,
    ValidationIssue,
)
from src.month_utils import get_month_dates

# ---------------------------------------------------------------------------
# Issue コード
# ---------------------------------------------------------------------------

STAFF_SHORTAGE = "STAFF_SHORTAGE"
STAFF_UNCERTAIN = "STAFF_UNCERTAIN"
STAFF_OVER_MAX = "STAFF_OVER_MAX"
STAFF_OVER_MAX_UNCERTAIN = "STAFF_OVER_MAX_UNCERTAIN"
ROLE_SHORTAGE = "ROLE_SHORTAGE"
ROLE_UNCERTAIN = "ROLE_UNCERTAIN"
# 各ロールを個別に見るだけでは発見できない組み合わせ不足（1人1ロール制約）
ROLE_COMBINATION_SHORTAGE = "ROLE_COMBINATION_SHORTAGE"
SKILL_SHORTAGE = "SKILL_SHORTAGE"
SKILL_UNCERTAIN = "SKILL_UNCERTAIN"
UNMATCHED_STAFF = "UNMATCHED_STAFF"
UNKNOWN_SHIFT = "UNKNOWN_SHIFT"
REQUIREMENT_MISSING = "REQUIREMENT_MISSING"

_SEVERITY_RANK = {
    VALIDATION_STATUS_OK: 0,
    VALIDATION_STATUS_INFO: 1,
    VALIDATION_STATUS_WARNING: 2,
    VALIDATION_STATUS_ERROR: 3,
}


def worst_status(issues: list[ValidationIssue]) -> str:
    """最も重大な severity（ERROR > WARNING > INFO > OK）. Issueなしは OK."""
    status = VALIDATION_STATUS_OK
    for issue in issues:
        if _SEVERITY_RANK[issue.severity] > _SEVERITY_RANK[status]:
            status = issue.severity
    return status


# ---------------------------------------------------------------------------
# 1日分の勤務者分類
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DayWorkers:
    """1日分の清掃関連勤務者. matched は staff master と照合できた人."""

    confirmed_matched: list[StaffInput]
    confirmed_unmatched: int
    uncertain_matched: list[StaffInput]
    uncertain_unmatched: int

    @property
    def actual(self) -> int:
        return len(self.confirmed_matched) + self.confirmed_unmatched

    @property
    def uncertain(self) -> int:
        return len(self.uncertain_matched) + self.uncertain_unmatched

    @property
    def unmatched(self) -> int:
        return self.confirmed_unmatched + self.uncertain_unmatched


def _classify(
    shifts: list[AttendanceShiftInput], staff_by_id: dict[int, StaffInput]
) -> _DayWorkers:
    confirmed_matched: list[StaffInput] = []
    uncertain_matched: list[StaffInput] = []
    confirmed_unmatched = 0
    uncertain_unmatched = 0
    for shift in shifts:
        if shift.available_for_cleaning:
            confirmed = True
        elif shift.shift_type == SHIFT_TYPE_UNKNOWN and is_cleaning_department(shift.department):
            confirmed = False
        else:
            continue  # OTHER_DUTY / BLANK / 清掃以外の部門

        staff = staff_by_id.get(shift.staff_id) if shift.staff_id is not None else None
        if staff is None:
            if confirmed:
                confirmed_unmatched += 1
            else:
                uncertain_unmatched += 1
        elif confirmed:
            confirmed_matched.append(staff)
        else:
            uncertain_matched.append(staff)
    return _DayWorkers(confirmed_matched, confirmed_unmatched, uncertain_matched, uncertain_unmatched)


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------


def _check_minimum(
    work_date: str, workers: _DayWorkers, required: int
) -> list[ValidationIssue]:
    actual, uncertain = workers.actual, workers.uncertain
    possible = actual + uncertain
    if actual >= required:
        return []
    if possible >= required:
        return [
            ValidationIssue(
                STAFF_UNCERTAIN,
                VALIDATION_STATUS_WARNING,
                f"清掃スタッフが不足している可能性があります（要確認）。"
                f"必要：{required}名／確定清掃勤務：{actual}名／勤務区分不明：{uncertain}名。"
                f"勤務区分不明の人が清掃勤務なら充足します。",
                work_date,
                required=required,
                actual=actual,
                possible=possible,
            )
        ]
    return [
        ValidationIssue(
            STAFF_SHORTAGE,
            VALIDATION_STATUS_ERROR,
            f"清掃スタッフが{required - possible}名以上不足しています。"
            f"必要：{required}名／確定清掃勤務：{actual}名／勤務区分不明：{uncertain}名。"
            f"最大でも{possible}名です。",
            work_date,
            required=required,
            actual=actual,
            possible=possible,
        )
    ]


def _check_maximum(
    work_date: str, workers: _DayWorkers, max_total: int | None
) -> list[ValidationIssue]:
    if max_total is None:
        return []
    actual, uncertain = workers.actual, workers.uncertain
    possible = actual + uncertain
    if actual > max_total:
        return [
            ValidationIssue(
                STAFF_OVER_MAX,
                VALIDATION_STATUS_ERROR,
                f"清掃スタッフが上限を{actual - max_total}名超えています。"
                f"上限：{max_total}名／確定清掃勤務：{actual}名。",
                work_date,
                required=max_total,
                actual=actual,
                possible=possible,
            )
        ]
    if possible > max_total:
        return [
            ValidationIssue(
                STAFF_OVER_MAX_UNCERTAIN,
                VALIDATION_STATUS_WARNING,
                f"清掃スタッフが上限を超える可能性があります（要確認）。"
                f"上限：{max_total}名／確定清掃勤務：{actual}名／勤務区分不明：{uncertain}名。",
                work_date,
                required=max_total,
                actual=actual,
                possible=possible,
            )
        ]
    return []


def _check_counted_requirement(
    work_date: str,
    *,
    required: int,
    known: int,
    possible: int,
    label: str,
    shortage_code: str,
    uncertain_code: str,
    role_id: int | None = None,
) -> list[ValidationIssue]:
    """ロール・スキル共通: known >= required → OK / possible >= required → WARNING / 他 → ERROR."""
    if required <= 0 or known >= required:
        return []
    if possible >= required:
        return [
            ValidationIssue(
                uncertain_code,
                VALIDATION_STATUS_WARNING,
                f"{label}が不足している可能性があります（要確認）。"
                f"必要：{required}名／確定：{known}名／未登録・勤務区分不明を含めた最大：{possible}名。",
                work_date,
                required=required,
                actual=known,
                possible=possible,
                role_id=role_id,
            )
        ]
    return [
        ValidationIssue(
            shortage_code,
            VALIDATION_STATUS_ERROR,
            f"{label}が{required - possible}名以上不足しています。"
            f"必要：{required}名／確定：{known}名／未登録・勤務区分不明を含めても最大{possible}名です。",
            work_date,
            required=required,
            actual=known,
            possible=possible,
            role_id=role_id,
        )
    ]


def _check_role_combination(
    work_date: str,
    workers: _DayWorkers,
    required_roles: dict[int, int],
    actual_roles: dict[int, int],
) -> list[ValidationIssue]:
    """1人1ロール制約で、全ロールを同時に満たせるかを判定する.

    ロール別判定では未登録スタッフを各ロールの候補として重複して数えるため、
    「未登録1名でリーダーもチェッカーも埋まる」ように見えてしまう。ここでは
    1. 各ロールの不足 = 必要 - 確定
    2. 同じロールの登録済みUNKNOWN勤務者で埋められる分を差し引く（ロールは確定しているため）
    3. 残った不足の合計を、ロール不明の未登録候補（清掃 TIME_RANGE / UNKNOWN）の人数と比較する
    残不足合計 > 未登録候補数 なら、楽観的に見ても同時には満たせないため ERROR。
    """
    fixed_uncertain = {role_id: 0 for role_id in required_roles}
    for staff in workers.uncertain_matched:
        if staff.role_id in fixed_uncertain:
            fixed_uncertain[staff.role_id] += 1

    total_deficit = 0
    remaining = 0
    for role_id, required in required_roles.items():
        deficit = max(0, required - actual_roles.get(role_id, 0))
        total_deficit += deficit
        remaining += max(0, deficit - fixed_uncertain[role_id])

    candidates = workers.unmatched
    if total_deficit == 0 or remaining <= candidates:
        return []
    return [
        ValidationIssue(
            ROLE_COMBINATION_SHORTAGE,
            VALIDATION_STATUS_ERROR,
            "ロール別には充足可能に見えますが、候補スタッフ数が不足しているため、"
            "必要なロールを同時に満たすことができません。"
            f"ロール不足合計：{remaining}名／割当可能な未登録候補：{candidates}名。",
            work_date,
            required=remaining,
            possible=candidates,
        )
    ]


def _data_quality_issues(work_date: str, workers: _DayWorkers) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if workers.uncertain:
        issues.append(
            ValidationIssue(
                UNKNOWN_SHIFT,
                VALIDATION_STATUS_WARNING,
                f"清掃所属で勤務区分が不明なセルが{workers.uncertain}件あります（清掃人数に含めていません）。",
                work_date,
                actual=workers.uncertain,
            )
        )
    if workers.unmatched:
        issues.append(
            ValidationIssue(
                UNMATCHED_STAFF,
                VALIDATION_STATUS_WARNING,
                f"スタッフマスター未登録の清掃スタッフが{workers.unmatched}名います"
                "（ロール・スキルは不明として判定しています）。",
                work_date,
                actual=workers.unmatched,
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_day(
    work_date: str,
    shifts: list[AttendanceShiftInput],
    requirement: DailyRequirementInput | None,
    role_requirements: dict[int, int],
    staff_by_id: dict[int, StaffInput],
    role_names: dict[int, str],
) -> DailyStaffingResult:
    """1日分を検証する. role_requirements は role_id -> 必要人数（行なしのロールは0）."""
    workers = _classify(shifts, staff_by_id)
    issues: list[ValidationIssue] = []

    actual_roles = {role_id: 0 for role_id in role_names}
    possible_roles = {role_id: 0 for role_id in role_names}
    flexible = workers.unmatched  # 未登録は任意のロールであり得る
    for role_id in role_names:
        known = sum(1 for s in workers.confirmed_matched if s.role_id == role_id)
        maybe = sum(1 for s in workers.uncertain_matched if s.role_id == role_id)
        actual_roles[role_id] = known
        possible_roles[role_id] = known + maybe + flexible

    if requirement is None:
        issues.append(
            ValidationIssue(
                REQUIREMENT_MISSING,
                VALIDATION_STATUS_WARNING,
                "この日の必要条件が未設定のため、人数・上限・ロール・スキルを判定できません"
                "（清掃不要日は最低人数0を登録してください）。",
                work_date,
            )
        )
        issues += _data_quality_issues(work_date, workers)
        return DailyStaffingResult(
            work_date=work_date,
            status=worst_status(issues),
            requirement_defined=False,
            actual_cleaning_staff=workers.actual,
            unmatched_working_count=workers.confirmed_unmatched,
            unknown_cleaning_shift_count=workers.uncertain,
            actual_roles=actual_roles,
            possible_roles=possible_roles,
            issues=issues,
        )

    issues += _check_minimum(work_date, workers, requirement.required_total_staff)
    issues += _check_maximum(work_date, workers, requirement.max_total_staff)

    required_roles = {role_id: role_requirements.get(role_id, 0) for role_id in role_names}
    role_issues: list[ValidationIssue] = []
    for role_id, required in required_roles.items():
        role_issues += _check_counted_requirement(
            work_date,
            required=required,
            known=actual_roles[role_id],
            possible=possible_roles[role_id],
            label=role_names[role_id],
            shortage_code=ROLE_SHORTAGE,
            uncertain_code=ROLE_UNCERTAIN,
            role_id=role_id,
        )
    issues += role_issues
    if not any(i.code == ROLE_SHORTAGE for i in role_issues):
        issues += _check_role_combination(work_date, workers, required_roles, actual_roles)

    level = requirement.required_skill_level
    skill_count = requirement.required_skill_count
    actual_skill = possible_skill = None
    if skill_count > 0 and level is not None:
        actual_skill = sum(1 for s in workers.confirmed_matched if s.skill_level >= level)
        maybe = sum(1 for s in workers.uncertain_matched if s.skill_level >= level)
        possible_skill = actual_skill + maybe + flexible
        issues += _check_counted_requirement(
            work_date,
            required=skill_count,
            known=actual_skill,
            possible=possible_skill,
            label=f"スキル{level}以上の清掃スタッフ",
            shortage_code=SKILL_SHORTAGE,
            uncertain_code=SKILL_UNCERTAIN,
        )

    issues += _data_quality_issues(work_date, workers)

    return DailyStaffingResult(
        work_date=work_date,
        status=worst_status(issues),
        requirement_defined=True,
        actual_cleaning_staff=workers.actual,
        unmatched_working_count=workers.confirmed_unmatched,
        unknown_cleaning_shift_count=workers.uncertain,
        required_staff=requirement.required_total_staff,
        max_total_staff=requirement.max_total_staff,
        required_roles=required_roles,
        actual_roles=actual_roles,
        possible_roles=possible_roles,
        required_skill_level=level,
        required_skill_count=skill_count,
        actual_skill_count=actual_skill,
        possible_skill_count=possible_skill,
        issues=issues,
    )


def validate_month_staffing(
    year_month: str,
    shifts: list[AttendanceShiftInput],
    daily_requirements: list[DailyRequirementInput],
    role_requirements: list[RoleRequirementInput],
    staff: list[StaffInput],
    role_names: dict[int, str],
) -> list[DailyStaffingResult]:
    """対象月の全日を検証する（勤務セルがない日も対象）.

    staff には無効スタッフも含めること（CSVに勤務があれば master の role/skill を使う）。
    """
    staff_by_id = {s.staff_id: s for s in staff}
    shifts_by_date: dict[str, list[AttendanceShiftInput]] = {}
    for shift in shifts:
        shifts_by_date.setdefault(shift.work_date, []).append(shift)
    requirement_by_date = {r.work_date: r for r in daily_requirements}
    roles_by_date: dict[str, dict[int, int]] = {}
    for r in role_requirements:
        roles_by_date.setdefault(r.work_date, {})[r.role_id] = r.required_count

    return [
        validate_day(
            work_date,
            shifts_by_date.get(work_date, []),
            requirement_by_date.get(work_date),
            roles_by_date.get(work_date, {}),
            staff_by_id,
            role_names,
        )
        for work_date in get_month_dates(year_month)
    ]
