"""画面から呼ばれる業務処理.

UIはこのモジュール経由でDB・解析・検証を組み合わせる。
"""

import dataclasses
import sqlite3

from src import repositories as repo
from src.attendance_import import parse_attendance_csv
from src.constants import CLEANING_DEPARTMENT, SHIFT_TYPE_BLANK, SHIFT_TYPE_UNKNOWN
from src.models import (
    AttendanceParseResult,
    AttendancePreview,
    AttendanceShiftInput,
    ImportIssue,
    StaffInput,
)

# 照合 warning コード（取込は可能）
UNMATCHED_STAFF = "ATTENDANCE_UNMATCHED_STAFF"
NAME_MISMATCH = "ATTENDANCE_NAME_MISMATCH"
DEPARTMENT_MISMATCH = "ATTENDANCE_DEPARTMENT_MISMATCH"
INACTIVE_STAFF = "ATTENDANCE_INACTIVE_STAFF"


def get_role_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["role_id"]: r["role_name"] for r in repo.list_roles(conn)}


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
            if s.department is not None and s.department.strip() == CLEANING_DEPARTMENT
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
