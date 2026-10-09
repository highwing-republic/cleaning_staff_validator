"""画面から呼ばれる業務処理.

UIはこのモジュール経由でDB・解析・検証を組み合わせる。
"""

import dataclasses
import sqlite3
from datetime import timedelta

from src import repositories as repo
from src.attendance_import import is_cleaning_department, parse_attendance_csv
from src.constants import (
    SHIFT_TYPE_BLANK,
    SHIFT_TYPE_UNKNOWN,
)
from src.constants import (
    SCHEDULE_RUN_INITIAL,
    SCHEDULE_SOURCE_GENERATED,
    SCHEDULE_SOURCE_MANUAL,
    TIME_STATUS_OK,
)
from src.day_conditions import resolve_period_conditions, resolve_staff_day_condition
from src.period_utils import parse_date
from src.models import (
    AttendanceParseResult,
    AttendancePreview,
    AttendanceShiftInput,
    DailyRequirementInput,
    DailyRequirementView,
    FixedAssignmentConflict,
    GenerationDay,
    GenerationRequest,
    GenerationStaff,
    HomeStatus,
    ImportIssue,
    MonthlyValidationResult,
    RegenerationPreview,
    RoleRequirementInput,
    ScheduleAssignmentRecord,
    ScheduleChange,
    ScheduleDayRecord,
    ScheduleDayView,
    ScheduleExport,
    ScheduleExportStaffRow,
    ScheduleGenerationResult,
    SchedulePeriodValidationResult,
    CurrentScheduleView,
    SpecialSkill,
    StaffDatePreferenceInput,
    StaffDayCondition,
    StaffDetail,
    StaffInput,
    ValidationError,
)
from src.schedule_excel import build_schedule_excel
from src.schedule_output_display import format_export_cell
from src.schedule_validation_adapter import validate_schedule_period
from src.shift_generation import generate_shift
from src.staffing_validation import validate_month_staffing
from src.validation import (
    PREFERENCE_STAFF_NOT_FOUND,
    SCHEDULE_ALREADY_EXISTS,
    SCHEDULE_ASSIGNMENT_NOT_FOUND,
    SCHEDULE_DAY_FINALIZED_READONLY,
    SCHEDULE_DAY_NOT_FOUND,
    SCHEDULE_NOTHING_TO_REGENERATE,
    SCHEDULE_STAFF_NOT_FOUND,
    SCHEDULE_STAFF_UNAVAILABLE,
    SCHEDULE_WORK_TIME_UNRESOLVED,
    STAFF_SPECIAL_SKILL_NOT_FOUND,
    validate_period_requirements,
    validate_staff,
    validate_staff_period_preferences,
)

# 照合 warning コード（取込は可能）
UNMATCHED_STAFF = "ATTENDANCE_UNMATCHED_STAFF"
NAME_MISMATCH = "ATTENDANCE_NAME_MISMATCH"
DEPARTMENT_MISMATCH = "ATTENDANCE_DEPARTMENT_MISMATCH"
INACTIVE_STAFF = "ATTENDANCE_INACTIVE_STAFF"


def get_role_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["role_id"]: r["role_name"] for r in repo.list_roles(conn)}


# ---------------------------------------------------------------------------
# スタッフマスター（通常勤務条件・特殊スキル）
# ---------------------------------------------------------------------------


def list_special_skill_options(
    conn: sqlite3.Connection,
    assigned_ids: list[int] | tuple[int, ...] = (),
) -> list[SpecialSkill]:
    """特殊スキルの選択肢を表示順で返す.

    無効（active=0）の特殊スキルは新規選択肢には出さない。ただし assigned_ids に
    すでに付与済みのものが含まれる場合は、編集時に黙って外れないよう残す。
    """
    assigned = set(assigned_ids)
    return [
        skill
        for skill in repo.list_special_skills(conn, include_inactive=True)
        if skill.active or skill.special_skill_id in assigned
    ]


def get_special_skill_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {s.special_skill_id: s.skill_name for s in repo.list_special_skills(conn)}


def validate_staff_master_input(
    conn: sqlite3.Connection,
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
    """スタッフ入力の検証（DBが必要な特殊スキルの存在確認を含む）."""
    errors = validate_staff(
        employee_code,
        staff_name,
        skill_level,
        standard_start_time=standard_start_time,
        standard_end_time=standard_end_time,
        target_days_per_week=target_days_per_week,
        max_days_per_period=max_days_per_period,
        max_consecutive_days=max_consecutive_days,
        weekdays=weekdays,
        special_skill_ids=special_skill_ids,
    )

    if special_skill_ids:
        known = {s.special_skill_id for s in repo.list_special_skills(conn)}
        unknown = sorted(set(special_skill_ids) - known)
        if unknown:
            errors.append(
                ValidationError(
                    code=STAFF_SPECIAL_SKILL_NOT_FOUND,
                    message="登録されていない特殊スキルが指定されています。",
                    field_name="special_skill_ids",
                )
            )
    return errors


# ---------------------------------------------------------------------------
# 勤怠CSV取込
# ---------------------------------------------------------------------------


def preview_attendance_csv(
    conn: sqlite3.Connection, data: bytes, source_filename: str
) -> AttendancePreview:
    """CSVを解析し staff master と照合したプレビューを返す（DBには書き込まない）."""
    return build_attendance_preview(conn, parse_attendance_csv(data, source_filename))


def build_attendance_preview(
    conn: sqlite3.Connection, parsed: AttendanceParseResult
) -> AttendancePreview:
    """解析結果を employee_code のみで staff master と照合する（氏名でのfallbackはしない）.

    - 未登録: staff_id=None のまま保持し warning。清掃可否はCSV部門で判定済みのため人数候補に残る
    - 氏名・部門の不一致 / 無効スタッフ: 照合は成功とし warning
    - 清掃可否は常にCSV側の部門を正とする（master の部門では上書きしない）
    """
    staff_by_code = {s.employee_code: s for s in repo.list_staff(conn, include_inactive=True)}

    employees = _employees_in_order(parsed.shifts)
    staff_id_by_code: dict[str, int] = {}
    warnings = list(parsed.warnings)
    unmatched: list[str] = []
    name_mismatch: list[str] = []
    department_mismatch: list[str] = []
    inactive: list[str] = []

    for code, first in employees.items():
        staff = staff_by_code.get(code)
        if staff is None:
            unmatched.append(code)
            warnings.append(
                ImportIssue(
                    UNMATCHED_STAFF,
                    f"未登録スタッフ: 従業員番号「{code}」はスタッフマスターにありません"
                    "（ロール・スキル不明として扱います）。",
                    employee_code=code,
                    employee_name=first.employee_name,
                )
            )
            continue

        staff_id_by_code[code] = staff.staff_id
        for issue in _match_warnings(staff, first):
            warnings.append(issue)
            {
                NAME_MISMATCH: name_mismatch,
                DEPARTMENT_MISMATCH: department_mismatch,
                INACTIVE_STAFF: inactive,
            }[issue.code].append(code)

    shifts = [
        dataclasses.replace(s, staff_id=staff_id_by_code.get(s.employee_code))
        for s in parsed.shifts
    ]

    return AttendancePreview(
        source_filename=parsed.source_filename,
        year_month=parsed.year_month,
        shifts=shifts,
        errors=list(parsed.errors),
        warnings=warnings,
        employee_count=len(employees),
        shift_count=sum(1 for s in shifts if s.shift_type != SHIFT_TYPE_BLANK),
        unmatched_count=len(unmatched),
        unknown_shift_count=sum(1 for s in shifts if s.shift_type == SHIFT_TYPE_UNKNOWN),
        cleaning_employee_count=sum(
            1
            for s in employees.values()
            if is_cleaning_department(s.department)
        ),
        name_mismatch_count=len(name_mismatch),
        department_mismatch_count=len(department_mismatch),
        inactive_staff_count=len(inactive),
    )


def import_attendance(conn: sqlite3.Connection, preview: AttendancePreview) -> int:
    """プレビュー内容を同月の有効版（ACTIVE）として保存し import_id を返す.

    fatal error があるプレビューは保存しない（DBを一切変更しない）。
    旧ACTIVEのSUPERSEDED化・新規保存は repository の1トランザクションで行われる。
    """
    if not preview.can_import:
        raise ValueError("取込できない内容です（エラーを解消してください）。")
    return repo.save_attendance_import(
        conn, preview.year_month, preview.source_filename, preview.shifts
    )


# ---------------------------------------------------------------------------
# 日別清掃体制検証
# ---------------------------------------------------------------------------

NO_ACTIVE_IMPORT_MESSAGE = "この月の勤怠シフトが取り込まれていません。"


def validate_month(conn: sqlite3.Connection, year_month: str) -> MonthlyValidationResult:
    """対象月のACTIVE取込を日別必要条件と比較する.

    ACTIVE取込がなければ検証エンジンを実行せず import_record=None の結果を返す。
    """
    active = repo.get_active_import(conn, year_month)
    if active is None:
        return MonthlyValidationResult(year_month=year_month)

    days = validate_month_staffing(
        year_month,
        repo.list_attendance_shifts(conn, active.import_id),
        repo.get_daily_requirements(conn, year_month),
        repo.get_role_requirements(conn, year_month),
        repo.list_staff(conn, include_inactive=True),
        get_role_names(conn),
    )
    return MonthlyValidationResult(year_month=year_month, import_record=active, days=days)


def _employees_in_order(shifts: list[AttendanceShiftInput]) -> dict[str, AttendanceShiftInput]:
    """employee_code -> その従業員の最初のセル（氏名・部門の参照用）. CSVの行順を保つ."""
    employees: dict[str, AttendanceShiftInput] = {}
    for shift in shifts:
        employees.setdefault(shift.employee_code, shift)
    return employees


def _match_warnings(staff: StaffInput, csv_row: AttendanceShiftInput) -> list[ImportIssue]:
    code = csv_row.employee_code
    csv_name = csv_row.employee_name
    csv_department = csv_row.department
    issues: list[ImportIssue] = []

    # CSV氏名が空欄の場合は解析時に「氏名空欄」warning 済みのため不一致としない
    if csv_name is not None and csv_name != staff.staff_name.strip():
        issues.append(
            ImportIssue(
                NAME_MISMATCH,
                f"氏名不一致: 従業員番号「{code}」CSV「{csv_name}」／マスター「{staff.staff_name}」",
                employee_code=code,
                employee_name=csv_name,
            )
        )

    # master の部門が未設定の場合は比較しない。清掃可否はCSV部門で判定する
    master_department = (staff.department or "").strip()
    if csv_department is not None and master_department and csv_department != master_department:
        issues.append(
            ImportIssue(
                DEPARTMENT_MISMATCH,
                f"部門不一致: 従業員番号「{code}」CSV「{csv_department}」／マスター「{master_department}」"
                "（清掃可否はCSVの部門で判定します）",
                employee_code=code,
                employee_name=csv_name,
            )
        )

    if not staff.active:
        issues.append(
            ImportIssue(
                INACTIVE_STAFF,
                f"無効スタッフ: 従業員番号「{code}」はマスターで無効ですが勤務データがあります"
                "（人数からは除外しません）。",
                employee_code=code,
                employee_name=csv_name,
            )
        )
    return issues


# ---------------------------------------------------------------------------
# 期間別勤務希望（Phase 6）
# ---------------------------------------------------------------------------


def get_period_conditions(
    conn: sqlite3.Connection,
    staff: StaffDetail,
    work_dates: list[str],
) -> list[StaffDayCondition]:
    """1スタッフの期間分の実効条件を求める（通常条件＋保存済みの希望）."""
    if not work_dates:
        return []
    saved = {
        p.work_date: p
        for p in repo.list_staff_preferences(
            conn, staff.staff.staff_id, work_dates[0], work_dates[-1]
        )
    }
    return resolve_period_conditions(staff, work_dates, saved)


def get_period_conditions_by_staff(
    conn: sqlite3.Connection,
    work_dates: list[str],
    include_inactive: bool = False,
) -> dict[int, list[StaffDayCondition]]:
    """スタッフ×日付の一覧表示用. 希望は期間分をまとめて1クエリで取得する."""
    if not work_dates:
        return {}

    staff_details = repo.list_staff_details(conn, include_inactive=include_inactive)
    saved: dict[int, dict[str, StaffDatePreferenceInput]] = {}
    for pref in repo.list_preferences_in_period(conn, work_dates[0], work_dates[-1]):
        saved.setdefault(pref.staff_id, {})[pref.work_date] = pref

    return {
        detail.staff.staff_id: resolve_period_conditions(
            detail, work_dates, saved.get(detail.staff.staff_id, {})
        )
        for detail in staff_details
    }


def validate_period_preferences(
    conn: sqlite3.Connection,
    staff_id: int,
    work_dates: list[str],
    preferences: list[StaffDatePreferenceInput],
) -> list[ValidationError]:
    """保存前の検証（スタッフ存在・日付・時刻・矛盾）."""
    staff = repo.get_staff_detail(conn, staff_id)
    if staff is None:
        return [
            ValidationError(
                code=PREFERENCE_STAFF_NOT_FOUND,
                message="対象のスタッフが見つかりません。",
                staff_id=staff_id,
                field_name="staff_id",
            )
        ]
    return validate_staff_period_preferences(staff_id, work_dates, preferences, staff)


def save_period_preferences(
    conn: sqlite3.Connection,
    staff_id: int,
    work_dates: list[str],
    preferences: list[StaffDatePreferenceInput],
) -> list[ValidationError]:
    """検証に通った場合のみ、1スタッフの期間分を1トランザクションで保存する.

    エラーがあれば何も保存せずエラー一覧を返す（空リスト = 保存成功）。
    """
    errors = validate_period_preferences(conn, staff_id, work_dates, preferences)
    if errors:
        return errors
    repo.save_staff_period_preferences(conn, staff_id, work_dates, preferences)
    return []


# ---------------------------------------------------------------------------
# 予約・必要人数（Phase 7）
# ---------------------------------------------------------------------------


def get_period_requirements(
    conn: sqlite3.Connection, work_dates: list[str]
) -> list[DailyRequirementView]:
    """対象期間の各日の要件を返す（行がない日は requirement=None = 要件未設定）.

    期間分をまとめて取得する（1日ずつ問い合わせない）。
    """
    if not work_dates:
        return []

    first, last = work_dates[0], work_dates[-1]
    requirements = {
        req.work_date: req for req in repo.list_daily_requirements(conn, first, last)
    }
    role_counts: dict[str, dict[int, int]] = {}
    for role_req in repo.list_role_requirements(conn, first, last):
        role_counts.setdefault(role_req.work_date, {})[role_req.role_id] = (
            role_req.required_count
        )

    return [
        DailyRequirementView(
            work_date=work_date,
            requirement=requirements.get(work_date),
            role_counts=role_counts.get(work_date, {}),
        )
        for work_date in work_dates
    ]


def save_period_requirements(
    conn: sqlite3.Connection,
    work_dates: list[str],
    requirements: list[DailyRequirementInput],
    role_requirements: list[RoleRequirementInput],
) -> list[ValidationError]:
    """検証に通った場合のみ、対象期間の予約・必要人数を1トランザクションで保存する.

    エラーがあれば何も保存せずエラー一覧を返す（空リスト = 保存成功）。
    requirements に含まれない日は要件未設定として行を削除する。
    """
    role_ids = {role["role_id"] for role in repo.list_roles(conn)}
    errors = validate_period_requirements(
        work_dates, requirements, role_requirements, role_ids
    )
    if errors:
        return errors
    repo.save_period_requirements(conn, work_dates, requirements, role_requirements)
    return []


# ---------------------------------------------------------------------------
# シフト生成（Phase 8）
# ---------------------------------------------------------------------------


def build_generation_request(
    conn: sqlite3.Connection,
    work_dates: list[str],
    prior_work_history: dict[int, set[str]] | None = None,
    time_limit_seconds: float | None = None,
    extra_staff_ids: set[int] | frozenset[int] = frozenset(),
) -> GenerationRequest:
    """DBから生成に必要なデータを集め、Solver用の入力へ変換する.

    Solver本体はDBを知らないため、通常勤務曜日・勤務希望はここで
    resolve_period_conditions による実効条件へ畳み込んで渡す。
    無効（active=0）のスタッフは候補に含めない。ただし extra_staff_ids に
    指定された無効スタッフは active=False のまま含める（再生成で確定日の
    勤務実績を数えるため。呼び出し側が全日固定して新たな勤務は与えない）。
    """
    if not work_dates:
        return GenerationRequest(work_dates=[], staff=[], days=[])

    first, last = work_dates[0], work_dates[-1]

    preferences_by_staff: dict[int, dict[str, StaffDatePreferenceInput]] = {}
    for pref in repo.list_preferences_in_period(conn, first, last):
        preferences_by_staff.setdefault(pref.staff_id, {})[pref.work_date] = pref

    role_codes = {row["role_id"]: row["role_code"] for row in repo.list_roles(conn)}
    staff: list[GenerationStaff] = []
    for detail in repo.list_staff_details(conn, include_inactive=True):
        if not detail.staff.active and detail.staff.staff_id not in extra_staff_ids:
            continue
        conditions = {
            condition.work_date: condition
            for condition in resolve_period_conditions(
                detail, work_dates, preferences_by_staff.get(detail.staff.staff_id, {})
            )
        }
        staff.append(
            GenerationStaff(
                staff_id=detail.staff.staff_id,
                employee_code=detail.staff.employee_code,
                staff_name=detail.staff.staff_name,
                role_id=detail.staff.role_id,
                skill_level=detail.staff.skill_level,
                active=bool(detail.staff.active),
                role_code=role_codes.get(detail.staff.role_id),
                day_conditions=conditions,
                max_consecutive_days=detail.staff.max_consecutive_days,
                target_days_per_week=detail.staff.target_days_per_week,
            )
        )

    days = [
        _to_generation_day(view) for view in get_period_requirements(conn, work_dates)
    ]

    return GenerationRequest(
        work_dates=list(work_dates),
        staff=staff,
        days=days,
        prior_work_history=prior_work_history or {},
        time_limit_seconds=time_limit_seconds,
    )


def _to_generation_day(view: DailyRequirementView) -> GenerationDay:
    """要件未設定の日は requirement_is_set=False で渡す（制約を設定しない）."""
    if not view.is_defined:
        return GenerationDay(work_date=view.work_date)

    requirement = view.requirement
    return GenerationDay(
        work_date=view.work_date,
        requirement_is_set=True,
        required_total_staff=requirement.required_total_staff,
        max_total_staff=requirement.max_total_staff,
        role_requirements=dict(view.role_counts),
        required_skill_level=requirement.required_skill_level,
        required_skill_count=requirement.required_skill_count,
        reserved_rooms=requirement.reserved_rooms,
    )


def generate_schedule(
    conn: sqlite3.Connection,
    work_dates: list[str],
    prior_work_history: dict[int, set[str]] | None = None,
    time_limit_seconds: float | None = None,
) -> ScheduleGenerationResult:
    """対象期間の勤務案を生成する（DBへは保存しない）."""
    request = build_generation_request(
        conn, work_dates, prior_work_history, time_limit_seconds
    )
    return generate_shift(request)


# ---------------------------------------------------------------------------
# 現在の勤務表（Phase 10）
# ---------------------------------------------------------------------------

# 連勤の期間境界判定でどこまで遡るか（スタッフ個別の上限が未設定のときの既定）
DEFAULT_PRIOR_LOOKBACK_DAYS = 7


def get_current_schedule(
    conn: sqlite3.Connection, work_dates: list[str]
) -> CurrentScheduleView:
    """対象期間の現在の勤務表を返す（Solverは実行しない）.

    勤務表がない日は day=None（未作成）として返す。
    """
    if not work_dates:
        return CurrentScheduleView(work_dates=[])

    first, last = work_dates[0], work_dates[-1]
    days = {d.work_date: d for d in repo.list_schedule_days(conn, first, last)}
    assignments: dict[str, dict[int, ScheduleAssignmentRecord]] = {}
    for assignment in repo.list_schedule_assignments(conn, first, last):
        assignments.setdefault(assignment.work_date, {})[assignment.staff_id] = assignment

    return CurrentScheduleView(
        work_dates=list(work_dates),
        days=[
            ScheduleDayView(
                work_date=work_date,
                day=days.get(work_date),
                assignments=assignments.get(work_date, {}),
            )
            for work_date in work_dates
        ],
    )


def _to_assignment_records(
    result: ScheduleGenerationResult,
) -> list[ScheduleAssignmentRecord]:
    """生成結果を勤務表のレコードへ変換する（勤務時刻はsnapshotとして保存）."""
    return [
        ScheduleAssignmentRecord(
            work_date=assignment.work_date,
            staff_id=assignment.staff_id,
            is_working=assignment.is_working,
            start_time=assignment.start_time if assignment.is_working else None,
            end_time=assignment.end_time if assignment.is_working else None,
            locked=False,
            source=SCHEDULE_SOURCE_GENERATED,
        )
        for assignment in result.assignments
    ]


def save_generated_schedule(
    conn: sqlite3.Connection,
    work_dates: list[str],
    result: ScheduleGenerationResult,
) -> tuple[int | None, list[ValidationError]]:
    """生成結果を下書きとして保存する（run_id, エラー）を返す.

    既存の勤務表がある日を含む場合は保存しない。無理にマージすると
    確定日や固定した勤務を取り違える恐れがあるため、勤務表調整画面からの
    再生成へ案内する。
    """
    if not result.has_solution:
        return None, [
            ValidationError(
                code=SCHEDULE_NOTHING_TO_REGENERATE,
                message="保存できる勤務案がありません。先に勤務案を作成してください。",
            )
        ]

    existing = get_current_schedule(conn, work_dates).existing_dates
    if existing:
        return None, [
            ValidationError(
                code=SCHEDULE_ALREADY_EXISTS,
                message=(
                    f"すでに勤務表がある日が{len(existing)}日あります"
                    "（勤務表調整画面から「固定を守って再生成」してください）。"
                ),
                work_date=existing[0],
            )
        ]

    run_id = repo.save_initial_schedule(
        conn,
        work_dates,
        _to_assignment_records(result),
        SCHEDULE_RUN_INITIAL,
        result.solver_status,
        result.total_shortage,
        result.total_workdays,
    )
    return run_id, []


# ---------------------------------------------------------------------------
# 手修正
# ---------------------------------------------------------------------------


def _load_manual_change_context(
    conn: sqlite3.Connection,
    staff_id: int,
    changes: dict[str, tuple[bool, bool]],
) -> tuple[
    StaffDetail | None,
    dict[str, StaffDatePreferenceInput],
    dict[str, ScheduleDayRecord],
    dict[str, ScheduleAssignmentRecord],
]:
    """手修正の検証・保存で共有する読み込み（期間をまとめて取得する）."""
    detail = repo.get_staff_detail(conn, staff_id)
    if detail is None or not changes:
        return detail, {}, {}, {}

    first, last = min(changes), max(changes)
    preferences = {
        p.work_date: p
        for p in repo.list_staff_preferences(conn, staff_id, first, last)
    }
    days = {d.work_date: d for d in repo.list_schedule_days(conn, first, last)}
    assignments = {
        a.work_date: a
        for a in repo.list_staff_schedule_assignments(conn, staff_id, first, last)
    }
    return detail, preferences, days, assignments


def validate_manual_schedule_changes(
    conn: sqlite3.Connection,
    staff_id: int,
    changes: dict[str, tuple[bool, bool]],
) -> list[ValidationError]:
    """手修正の検証. changes は work_date -> (出勤するか, 固定するか).

    休み→出勤はHard Constraintを破れないので、勤務可能で勤務時刻が確定する日だけ許す。
    出勤→休みは必要人数が不足しても許可する（不足は画面で確認できる）。
    """
    detail, preferences, days, assignments = _load_manual_change_context(
        conn, staff_id, changes
    )
    return _validate_manual_changes(
        detail, preferences, days, assignments, staff_id, changes
    )


def _validate_manual_changes(
    detail: StaffDetail | None,
    preferences: dict[str, StaffDatePreferenceInput],
    days: dict[str, ScheduleDayRecord],
    assignments: dict[str, ScheduleAssignmentRecord],
    staff_id: int,
    changes: dict[str, tuple[bool, bool]],
) -> list[ValidationError]:
    if detail is None:
        return [
            ValidationError(
                code=SCHEDULE_STAFF_NOT_FOUND,
                message="対象のスタッフが見つかりません。",
                staff_id=staff_id,
                field_name="staff_id",
            )
        ]

    errors: list[ValidationError] = []

    for work_date, (is_working, _locked) in sorted(changes.items()):
        day = days.get(work_date)
        if day is None:
            errors.append(
                ValidationError(
                    code=SCHEDULE_DAY_NOT_FOUND,
                    message="この日の勤務表がまだ作成されていません。",
                    staff_id=staff_id,
                    work_date=work_date,
                )
            )
            continue
        if day.is_finalized:
            errors.append(
                ValidationError(
                    code=SCHEDULE_DAY_FINALIZED_READONLY,
                    message="確定済みの日は変更できません（確定を解除してください）。",
                    staff_id=staff_id,
                    work_date=work_date,
                )
            )
            continue
        if assignments.get(work_date) is None:
            errors.append(
                ValidationError(
                    code=SCHEDULE_ASSIGNMENT_NOT_FOUND,
                    message="この日のこのスタッフの勤務表が見つかりません。",
                    staff_id=staff_id,
                    work_date=work_date,
                )
            )
            continue

        if not is_working:
            continue

        condition = resolve_staff_day_condition(detail, work_date, preferences.get(work_date))
        if not condition.can_work:
            errors.append(
                ValidationError(
                    code=SCHEDULE_STAFF_UNAVAILABLE,
                    message=(
                        "この日は勤務できません"
                        "（絶対休み、または通常勤務しない曜日です）。"
                    ),
                    staff_id=staff_id,
                    work_date=work_date,
                )
            )
        elif condition.time_status != TIME_STATUS_OK:
            errors.append(
                ValidationError(
                    code=SCHEDULE_WORK_TIME_UNRESOLVED,
                    message=(
                        "この日の勤務時間を確定できません"
                        "（通常勤務時間を登録してください）。"
                    ),
                    staff_id=staff_id,
                    work_date=work_date,
                )
            )

    return errors


def save_manual_schedule_changes(
    conn: sqlite3.Connection,
    staff_id: int,
    changes: dict[str, tuple[bool, bool]],
) -> list[ValidationError]:
    """手修正を保存する（検証に通った場合のみ1トランザクション）.

    勤務時刻は resolve_staff_day_condition の実効時間から決める
    （Phase 10では時刻の直接入力は行わない）。
    """
    if not changes:
        return []

    detail, preferences, days, assignments = _load_manual_change_context(
        conn, staff_id, changes
    )
    errors = _validate_manual_changes(
        detail, preferences, days, assignments, staff_id, changes
    )
    if errors:
        return errors

    records = []
    for work_date, (is_working, locked) in sorted(changes.items()):
        if is_working:
            condition = resolve_staff_day_condition(
                detail, work_date, preferences.get(work_date)
            )
            start_time = condition.effective_start_time
            end_time = condition.effective_end_time
        else:
            start_time = end_time = None
        records.append(
            ScheduleAssignmentRecord(
                work_date=work_date,
                staff_id=staff_id,
                is_working=is_working,
                start_time=start_time,
                end_time=end_time,
                locked=locked,
                source=SCHEDULE_SOURCE_MANUAL,
            )
        )

    repo.save_staff_manual_changes(conn, staff_id, records)
    return []


# ---------------------------------------------------------------------------
# 再生成（固定を守る）
# ---------------------------------------------------------------------------


def build_regeneration_request(
    conn: sqlite3.Connection,
    work_dates: list[str],
    schedule: CurrentScheduleView | None = None,
    time_limit_seconds: float | None = None,
) -> GenerationRequest:
    """現在の勤務表の固定・確定を反映した再生成の入力を作る.

    DRAFT日は locked のセルだけ固定、確定日はその日の全スタッフを固定する。
    連勤の期間境界は確定済みの勤務表から求める（DRAFTは使わない）。
    勤務表に行がある無効スタッフも含める（確定日の勤務実績を人数・ロール・
    スキルの充足として数えるため）。無効スタッフは固定されていない日を
    すべて休みで固定し、新たな勤務は割り当てない。
    """
    current = schedule or get_current_schedule(conn, work_dates)
    fixed: dict[tuple[int, str], int] = {}

    for view in current.days:
        if not view.exists:
            continue
        for staff_id, assignment in view.assignments.items():
            if view.is_finalized or assignment.locked:
                fixed[(staff_id, view.work_date)] = int(assignment.is_working)

    scheduled_ids = {
        staff_id for view in current.days for staff_id in view.assignments
    }
    request = build_generation_request(
        conn,
        work_dates,
        prior_work_history=build_prior_work_history(conn, work_dates),
        time_limit_seconds=time_limit_seconds,
        extra_staff_ids=scheduled_ids,
    )
    for staff in request.staff:
        if staff.active:
            continue
        for work_date in work_dates:
            fixed.setdefault((staff.staff_id, work_date), 0)

    return dataclasses.replace(request, fixed_assignments=fixed)


def build_prior_work_history(
    conn: sqlite3.Connection, work_dates: list[str]
) -> dict[int, set[str]]:
    """対象期間の直前について、確定済みの勤務日を staff_id ごとに返す.

    遡る日数はスタッフの最大連勤日数の最大値で足りる。
    DRAFTの日は含めない（まだ変更される可能性があるため）。
    """
    if not work_dates:
        return {}

    limits = [
        detail.staff.max_consecutive_days
        for detail in repo.list_staff_details(conn, include_inactive=False)
        if detail.staff.max_consecutive_days
    ]
    lookback = max(limits) if limits else DEFAULT_PRIOR_LOOKBACK_DAYS

    start = parse_date(work_dates[0])
    if start is None:
        return {}
    first = (start - timedelta(days=lookback)).isoformat()
    last = (start - timedelta(days=1)).isoformat()
    return repo.list_finalized_working_dates(conn, first, last)


def _detect_fixed_conflicts(
    request: GenerationRequest,
) -> list[FixedAssignmentConflict]:
    """固定された勤務と現在の勤務可能条件の矛盾を検出する（解除はしない）."""
    staff_by_id = {staff.staff_id: staff for staff in request.staff}
    conflicts: list[FixedAssignmentConflict] = []

    for (staff_id, work_date), value in sorted(request.fixed_assignments.items()):
        if not value:
            continue
        staff = staff_by_id.get(staff_id)
        if staff is None:
            continue
        condition = staff.day_conditions.get(work_date)
        if condition is None:
            continue
        if not condition.can_work:
            reason = "勤務できない日（絶対休み、または通常勤務しない曜日）に出勤で固定されています。"
        elif condition.time_status != TIME_STATUS_OK:
            reason = "勤務時間を確定できない日に出勤で固定されています。"
        else:
            continue
        conflicts.append(
            FixedAssignmentConflict(
                work_date=work_date,
                staff_id=staff_id,
                staff_name=staff.staff_name,
                reason=reason,
            )
        )
    return conflicts


def _regeneration_updates(
    current: CurrentScheduleView,
    result: ScheduleGenerationResult,
) -> tuple[list[ScheduleAssignmentRecord], list[ScheduleChange]]:
    """再生成で書き換えるセルと、その変更一覧を同じ判定で作る.

    プレビューに出ないセルは反映でも書かない（出欠・時刻とも変わらない
    セルは手修正の時刻・sourceをそのまま保つ）。行がない有効スタッフの
    セルは新規作成する（SCHEDULE_INCOMPLETE の案内どおり行を揃えるため）。
    """
    views = {view.work_date: view for view in current.days}
    records: list[ScheduleAssignmentRecord] = []
    changes: list[ScheduleChange] = []

    for assignment in result.assignments:
        view = views.get(assignment.work_date)
        if view is None or not view.is_editable:
            continue
        existing = view.assignments.get(assignment.staff_id)
        if existing is not None and existing.locked:
            continue
        start_time = assignment.start_time if assignment.is_working else None
        end_time = assignment.end_time if assignment.is_working else None
        if (
            existing is not None
            and existing.is_working == assignment.is_working
            and existing.start_time == start_time
            and existing.end_time == end_time
        ):
            continue
        records.append(
            ScheduleAssignmentRecord(
                work_date=assignment.work_date,
                staff_id=assignment.staff_id,
                is_working=assignment.is_working,
                start_time=start_time,
                end_time=end_time,
                locked=False,
                source=SCHEDULE_SOURCE_GENERATED,
            )
        )
        changes.append(
            ScheduleChange(
                work_date=assignment.work_date,
                staff_id=assignment.staff_id,
                staff_name=assignment.staff_name,
                before_is_working=existing.is_working if existing else False,
                after_is_working=assignment.is_working,
                before_start_time=existing.start_time if existing else None,
                before_end_time=existing.end_time if existing else None,
                after_start_time=start_time,
                after_end_time=end_time,
                is_new_row=existing is None,
            )
        )

    return records, changes


def preview_regenerated_schedule(
    conn: sqlite3.Connection,
    work_dates: list[str],
    time_limit_seconds: float | None = None,
) -> RegenerationPreview:
    """再生成の下見を作る（DBは一切変更しない）.

    Solverへは勤務表がある日だけを渡す。未作成の日まで渡すと、反映されない
    日に目標勤務日数や連勤の枠を消費した勤務案ができてしまい、保存結果が
    最適化結果と食い違うため。
    """
    current = get_current_schedule(conn, work_dates)
    target_dates = current.draft_dates

    if not current.existing_dates:
        return RegenerationPreview(
            work_dates=list(work_dates),
            errors=[
                ValidationError(
                    code=SCHEDULE_DAY_NOT_FOUND,
                    message="対象期間に勤務表がありません。先に勤務案を下書き保存してください。",
                )
            ],
        )
    if not target_dates:
        return RegenerationPreview(
            work_dates=list(work_dates),
            skipped_finalized_dates=current.finalized_dates,
            errors=[
                ValidationError(
                    code=SCHEDULE_NOTHING_TO_REGENERATE,
                    message="対象期間はすべて確定済みです（確定を解除すると再生成できます）。",
                )
            ],
        )

    request = build_regeneration_request(
        conn,
        current.existing_dates,
        schedule=current,
        time_limit_seconds=time_limit_seconds,
    )
    conflicts = _detect_fixed_conflicts(request)
    result = generate_shift(request)

    changes: list[ScheduleChange] = []
    if result.has_solution:
        _, changes = _regeneration_updates(current, result)

    return RegenerationPreview(
        work_dates=list(work_dates),
        result=result,
        changes=changes,
        conflicts=conflicts,
        target_dates=target_dates,
        skipped_finalized_dates=current.finalized_dates,
        skipped_missing_dates=current.missing_dates,
    )


def apply_regenerated_schedule(
    conn: sqlite3.Connection,
    work_dates: list[str],
    preview: RegenerationPreview,
) -> tuple[int | None, list[ValidationError]]:
    """再生成結果を反映する（run_id, エラー）を返す.

    更新するのはDRAFT日の固定されていないセルのうち内容が変わるものだけ。
    固定されたセルと確定日は現在値をそのまま残す。
    """
    if not preview.can_apply:
        return None, preview.errors or [
            ValidationError(
                code=SCHEDULE_NOTHING_TO_REGENERATE,
                message="反映できる再生成結果がありません。",
            )
        ]

    current = get_current_schedule(conn, work_dates)
    records, _changes = _regeneration_updates(current, preview.result)

    run_id = repo.apply_regenerated_schedule(
        conn,
        preview.result.work_dates,
        records,
        preview.result.solver_status,
        preview.result.total_shortage,
        preview.result.total_workdays,
    )
    return run_id, []


# ---------------------------------------------------------------------------
# 日別の確定
# ---------------------------------------------------------------------------


def finalize_schedule_day(
    conn: sqlite3.Connection, work_date: str
) -> list[ValidationError]:
    day = repo.get_schedule_day(conn, work_date)
    if day is None:
        return [
            ValidationError(
                code=SCHEDULE_DAY_NOT_FOUND,
                message="この日の勤務表がまだ作成されていません。",
                work_date=work_date,
            )
        ]
    repo.finalize_schedule_day(conn, work_date)
    return []


def unfinalize_schedule_day(
    conn: sqlite3.Connection, work_date: str
) -> list[ValidationError]:
    day = repo.get_schedule_day(conn, work_date)
    if day is None:
        return [
            ValidationError(
                code=SCHEDULE_DAY_NOT_FOUND,
                message="この日の勤務表がまだ作成されていません。",
                work_date=work_date,
            )
        ]
    repo.unfinalize_schedule_day(conn, work_date)
    return []



# ---------------------------------------------------------------------------
# 現在勤務表の検証・出力（Phase 11）
# ---------------------------------------------------------------------------


def validate_current_schedule(
    conn: sqlite3.Connection, work_dates: list[str]
) -> SchedulePeriodValidationResult:
    """保存済み勤務表を検証する（毎回DBから読み直すので手修正が即反映される）.

    検証対象は schedule_assignments で、勤怠CSV（attendance_shifts）とは別概念。
    ロール・スキルはスタッフマスターの現在値を使う。
    """
    if not work_dates:
        return SchedulePeriodValidationResult(work_dates=[])

    schedule = get_current_schedule(conn, work_dates)
    first, last = work_dates[0], work_dates[-1]
    staff = repo.list_staff(conn, include_inactive=True)
    return validate_schedule_period(
        work_dates,
        schedule,
        repo.list_daily_requirements(conn, first, last),
        repo.list_role_requirements(conn, first, last),
        staff,
        get_role_names(conn),
        active_staff_ids=frozenset(s.staff_id for s in staff if s.active),
    )


def _export_staff_order(
    staff: list[StaffInput], scheduled_ids: set[int]
) -> list[StaffInput]:
    """勤務表に載せるスタッフを安定した順番で返す.

    有効スタッフに加えて、勤務表に行がある無効スタッフも落とさない
    （当時の勤務表から人が消えないようにするため）。
    """
    targets = [s for s in staff if s.active or s.staff_id in scheduled_ids]
    return sorted(targets, key=lambda s: (s.role_id, s.employee_code, s.staff_id))


def build_schedule_export(
    conn: sqlite3.Connection,
    work_dates: list[str],
    exported_at: str | None = None,
) -> ScheduleExport:
    """勤務表Excelの材料を集める（押した時点の現在勤務表を使う）."""
    validation = validate_current_schedule(conn, work_dates)
    if not work_dates:
        return ScheduleExport(
            work_dates=[], validation=validation, exported_at=exported_at
        )

    schedule = get_current_schedule(conn, work_dates)
    role_names = get_role_names(conn)
    staff = repo.list_staff(conn, include_inactive=True)
    scheduled_ids = {
        staff_id
        for day in schedule.days
        for staff_id in day.assignments
    }

    views = {view.work_date: view for view in schedule.days}
    rows: list[ScheduleExportStaffRow] = []
    for member in _export_staff_order(staff, scheduled_ids):
        cells = {}
        for work_date in work_dates:
            view = views.get(work_date)
            exists = view is not None and view.exists
            assignment = view.assignments.get(member.staff_id) if view else None
            cells[work_date] = format_export_cell(assignment, exists)
        rows.append(
            ScheduleExportStaffRow(
                staff=member,
                role_name=role_names.get(member.role_id, str(member.role_id)),
                cells=cells,
            )
        )

    return ScheduleExport(
        work_dates=list(work_dates),
        validation=validation,
        staff_rows=rows,
        role_names=role_names,
        exported_at=exported_at,
    )


def export_schedule_excel(
    conn: sqlite3.Connection,
    work_dates: list[str],
    exported_at: str | None = None,
) -> bytes:
    """勤務表Excelを bytes で返す（DBには保存しない）."""
    return build_schedule_excel(
        build_schedule_export(conn, work_dates, exported_at=exported_at)
    )


# ---------------------------------------------------------------------------
# ホーム画面の進行状況（Phase 12）
# ---------------------------------------------------------------------------


def get_home_status(conn: sqlite3.Connection, work_dates: list[str]) -> HomeStatus:
    """対象期間について、どこまで入力・作成が進んでいるかを数える."""
    staff = repo.list_staff(conn, include_inactive=False)
    without_time = sum(
        1
        for s in staff
        if s.standard_start_time is None or s.standard_end_time is None
    )
    if not work_dates:
        return HomeStatus(
            work_dates=[],
            active_staff_count=len(staff),
            staff_without_work_time=without_time,
        )

    first, last = work_dates[0], work_dates[-1]
    target = set(work_dates)
    preference_days = len(
        {
            p.work_date
            for p in repo.list_preferences_in_period(conn, first, last)
            if p.work_date in target
        }
    )
    requirement_days = sum(
        1
        for r in repo.list_daily_requirements(conn, first, last)
        if r.work_date in target
    )
    days = [d for d in repo.list_schedule_days(conn, first, last) if d.work_date in target]
    return HomeStatus(
        work_dates=list(work_dates),
        active_staff_count=len(staff),
        staff_without_work_time=without_time,
        preference_days=preference_days,
        requirement_days=requirement_days,
        schedule_days=len(days),
        finalized_days=sum(1 for d in days if d.is_finalized),
    )
