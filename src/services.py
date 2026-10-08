"""画面から呼ばれる業務処理.

UIはこのモジュール経由でDB・解析・検証を組み合わせる。
"""

import dataclasses
import sqlite3

from src import repositories as repo
from src.attendance_import import is_cleaning_department, parse_attendance_csv
from src.constants import SHIFT_TYPE_BLANK, SHIFT_TYPE_UNKNOWN
from src.day_conditions import resolve_period_conditions
from src.models import (
    AttendanceParseResult,
    AttendancePreview,
    AttendanceShiftInput,
    DailyRequirementInput,
    DailyRequirementView,
    GenerationDay,
    GenerationRequest,
    GenerationStaff,
    ImportIssue,
    MonthlyValidationResult,
    RoleRequirementInput,
    ScheduleGenerationResult,
    SpecialSkill,
    StaffDatePreferenceInput,
    StaffDayCondition,
    StaffDetail,
    StaffInput,
    ValidationError,
)
from src.shift_generation import generate_shift
from src.staffing_validation import validate_month_staffing
from src.validation import (
    PREFERENCE_STAFF_NOT_FOUND,
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
) -> GenerationRequest:
    """DBから生成に必要なデータを集め、Solver用の入力へ変換する.

    Solver本体はDBを知らないため、通常勤務曜日・勤務希望はここで
    resolve_period_conditions による実効条件へ畳み込んで渡す。
    無効（active=0）のスタッフは候補に含めない。
    """
    if not work_dates:
        return GenerationRequest(work_dates=[], staff=[], days=[])

    first, last = work_dates[0], work_dates[-1]

    preferences_by_staff: dict[int, dict[str, StaffDatePreferenceInput]] = {}
    for pref in repo.list_preferences_in_period(conn, first, last):
        preferences_by_staff.setdefault(pref.staff_id, {})[pref.work_date] = pref

    staff: list[GenerationStaff] = []
    for detail in repo.list_staff_details(conn, include_inactive=False):
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

