"""DBアクセス層.

業務検証（validation）は行わない。DB制約に委ねる。
ただし本ファイル内で明示された検査のみ行う。
"""

import sqlite3
from datetime import datetime

from src.constants import (
    IMPORT_STATUS_ACTIVE,
    IMPORT_STATUS_SUPERSEDED,
    SHIFT_TYPE_BLANK,
    SKILL_LEVEL_DEFAULT,
)
from src.models import (
    AttendanceImportRecord,
    AttendanceShiftInput,
    DailyRequirementInput,
    RoleRequirementInput,
    StaffInput,
)
from src.month_utils import get_month_dates


# ---------------------------------------------------------------------------
# roles / staff
# ---------------------------------------------------------------------------


def list_roles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT role_id, role_code, role_name, active FROM roles ORDER BY role_id"
    ).fetchall()
    return [dict(row) for row in rows]


def _row_to_staff_input(row: sqlite3.Row) -> StaffInput:
    return StaffInput(
        staff_id=row["staff_id"],
        employee_code=row["employee_code"],
        staff_name=row["staff_name"],
        role_id=row["role_id"],
        skill_level=row["skill_level"],
        department=row["department"],
        active=bool(row["active"]),
    )


def list_staff(conn: sqlite3.Connection, include_inactive: bool = True) -> list[StaffInput]:
    if include_inactive:
        rows = conn.execute("SELECT * FROM staff ORDER BY staff_id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM staff WHERE active = 1 ORDER BY staff_id"
        ).fetchall()
    return [_row_to_staff_input(row) for row in rows]


def get_staff(conn: sqlite3.Connection, staff_id: int) -> StaffInput | None:
    row = conn.execute(
        "SELECT * FROM staff WHERE staff_id = ?", (staff_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_staff_input(row)


def get_staff_by_employee_code(
    conn: sqlite3.Connection, employee_code: str
) -> StaffInput | None:
    """従業員番号（文字列の完全一致）でスタッフを取得する."""
    row = conn.execute(
        "SELECT * FROM staff WHERE employee_code = ?", (employee_code,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_staff_input(row)


def create_staff(
    conn: sqlite3.Connection,
    employee_code: str,
    staff_name: str,
    role_id: int,
    skill_level: int = SKILL_LEVEL_DEFAULT,
    department: str | None = None,
) -> int:
    """スタッフを作成する. employee_code 重複時は sqlite3.IntegrityError."""
    with conn:
        cur = conn.execute(
            "INSERT INTO staff (employee_code, staff_name, department, role_id, skill_level) "
            "VALUES (?, ?, ?, ?, ?)",
            (employee_code, staff_name, department, role_id, skill_level),
        )
    return cur.lastrowid


def update_staff(
    conn: sqlite3.Connection,
    staff_id: int,
    *,
    employee_code: str,
    staff_name: str,
    role_id: int,
    skill_level: int,
    department: str | None = None,
) -> None:
    with conn:
        cur = conn.execute(
            "UPDATE staff SET employee_code = ?, staff_name = ?, department = ?, "
            "role_id = ?, skill_level = ? WHERE staff_id = ?",
            (employee_code, staff_name, department, role_id, skill_level, staff_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"staff not found: {staff_id!r}")


def deactivate_staff(conn: sqlite3.Connection, staff_id: int) -> None:
    with conn:
        cur = conn.execute(
            "UPDATE staff SET active = 0 WHERE staff_id = ?", (staff_id,)
        )
        if cur.rowcount == 0:
            raise ValueError(f"staff not found: {staff_id!r}")


# ---------------------------------------------------------------------------
# requirements
# ---------------------------------------------------------------------------

_UPSERT_DAILY_REQUIREMENT_SQL = """
    INSERT INTO daily_requirements (
        work_date, occupancy_rate, required_total_staff, max_total_staff, note,
        required_skill_level, required_skill_count
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (work_date) DO UPDATE SET
        occupancy_rate = excluded.occupancy_rate,
        required_total_staff = excluded.required_total_staff,
        max_total_staff = excluded.max_total_staff,
        note = excluded.note,
        required_skill_level = excluded.required_skill_level,
        required_skill_count = excluded.required_skill_count
"""


def _daily_requirement_params(req: DailyRequirementInput) -> tuple:
    return (
        req.work_date,
        req.occupancy_rate,
        req.required_total_staff,
        req.max_total_staff,
        req.note,
        req.required_skill_level,
        req.required_skill_count,
    )


def get_daily_requirements(
    conn: sqlite3.Connection, year_month: str
) -> list[DailyRequirementInput]:
    dates = get_month_dates(year_month)
    first, last = dates[0], dates[-1]
    rows = conn.execute(
        "SELECT * FROM daily_requirements WHERE work_date BETWEEN ? AND ? "
        "ORDER BY work_date",
        (first, last),
    ).fetchall()
    return [
        DailyRequirementInput(
            work_date=row["work_date"],
            required_total_staff=row["required_total_staff"],
            max_total_staff=row["max_total_staff"],
            occupancy_rate=row["occupancy_rate"],
            note=row["note"],
            required_skill_level=row["required_skill_level"],
            required_skill_count=row["required_skill_count"],
        )
        for row in rows
    ]


def save_daily_requirement(conn: sqlite3.Connection, req: DailyRequirementInput) -> None:
    with conn:
        conn.execute(_UPSERT_DAILY_REQUIREMENT_SQL, _daily_requirement_params(req))


def save_daily_requirements(
    conn: sqlite3.Connection, reqs: list[DailyRequirementInput]
) -> None:
    """全件成功時のみ保存する一括upsert."""
    with conn:
        for req in reqs:
            conn.execute(_UPSERT_DAILY_REQUIREMENT_SQL, _daily_requirement_params(req))


def get_role_requirements(conn: sqlite3.Connection, year_month: str) -> list[RoleRequirementInput]:
    dates = get_month_dates(year_month)
    first, last = dates[0], dates[-1]
    rows = conn.execute(
        "SELECT work_date, role_id, required_count FROM daily_role_requirements "
        "WHERE work_date BETWEEN ? AND ? ORDER BY work_date, role_id",
        (first, last),
    ).fetchall()
    return [
        RoleRequirementInput(row["work_date"], row["role_id"], row["required_count"])
        for row in rows
    ]


def save_role_requirement(conn: sqlite3.Connection, req: RoleRequirementInput) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO daily_role_requirements (work_date, role_id, required_count)
            VALUES (?, ?, ?)
            ON CONFLICT (work_date, role_id) DO UPDATE SET
                required_count = excluded.required_count
            """,
            (req.work_date, req.role_id, req.required_count),
        )


def save_role_requirements(conn: sqlite3.Connection, reqs: list[RoleRequirementInput]) -> None:
    with conn:
        for req in reqs:
            conn.execute(
                """
                INSERT INTO daily_role_requirements (work_date, role_id, required_count)
                VALUES (?, ?, ?)
                ON CONFLICT (work_date, role_id) DO UPDATE SET
                    required_count = excluded.required_count
                """,
                (req.work_date, req.role_id, req.required_count),
            )


# ---------------------------------------------------------------------------
# attendance
# ---------------------------------------------------------------------------


def _row_to_import_record(row: sqlite3.Row) -> AttendanceImportRecord:
    return AttendanceImportRecord(
        import_id=row["import_id"],
        year_month=row["year_month"],
        source_filename=row["source_filename"],
        imported_at=row["imported_at"],
        employee_count=row["employee_count"],
        shift_count=row["shift_count"],
        unmatched_count=row["unmatched_count"],
        status=row["status"],
    )


def save_attendance_import(
    conn: sqlite3.Connection,
    year_month: str,
    source_filename: str,
    shifts: list[AttendanceShiftInput],
) -> int:
    """勤怠取込を1トランザクションで保存し、新しい import_id を返す.

    - 同月の旧ACTIVEは物理削除せず SUPERSEDED にする。
    - 途中で失敗した場合は全体がロールバックされ、旧ACTIVEはそのまま残る。
    - 集計値: employee_count=従業員番号の種類数, unmatched_count=うちstaff未照合の種類数,
      shift_count=BLANK以外のセル数。
    """
    valid_dates = set(get_month_dates(year_month))
    for shift in shifts:
        if shift.work_date not in valid_dates:
            raise ValueError(
                f"shift work_date {shift.work_date!r} is not in {year_month!r}"
            )

    employee_codes = {s.employee_code for s in shifts}
    unmatched_codes = {s.employee_code for s in shifts if s.staff_id is None}
    shift_count = sum(1 for s in shifts if s.shift_type != SHIFT_TYPE_BLANK)
    imported_at = datetime.now().isoformat(timespec="seconds")

    with conn:
        conn.execute(
            "UPDATE attendance_imports SET status = ? WHERE year_month = ? AND status = ?",
            (IMPORT_STATUS_SUPERSEDED, year_month, IMPORT_STATUS_ACTIVE),
        )
        cur = conn.execute(
            "INSERT INTO attendance_imports (year_month, source_filename, imported_at, "
            "employee_count, shift_count, unmatched_count, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                year_month,
                source_filename,
                imported_at,
                len(employee_codes),
                shift_count,
                len(unmatched_codes),
                IMPORT_STATUS_ACTIVE,
            ),
        )
        import_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO attendance_shifts (import_id, staff_id, employee_code, employee_name, "
            "department, work_date, raw_shift, shift_type, start_minutes, end_minutes, "
            "available_for_cleaning) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    import_id,
                    s.staff_id,
                    s.employee_code,
                    s.employee_name,
                    s.department,
                    s.work_date,
                    s.raw_shift,
                    s.shift_type,
                    s.start_minutes,
                    s.end_minutes,
                    int(s.available_for_cleaning),
                )
                for s in shifts
            ],
        )
    return import_id


def get_active_import(
    conn: sqlite3.Connection, year_month: str
) -> AttendanceImportRecord | None:
    row = conn.execute(
        "SELECT * FROM attendance_imports WHERE year_month = ? AND status = ?",
        (year_month, IMPORT_STATUS_ACTIVE),
    ).fetchone()
    if row is None:
        return None
    return _row_to_import_record(row)


def list_attendance_imports(
    conn: sqlite3.Connection, year_month: str
) -> list[AttendanceImportRecord]:
    """対象月の取込履歴を新しい順で返す（SUPERSEDEDを含む）."""
    rows = conn.execute(
        "SELECT * FROM attendance_imports WHERE year_month = ? ORDER BY import_id DESC",
        (year_month,),
    ).fetchall()
    return [_row_to_import_record(row) for row in rows]


def list_attendance_shifts(
    conn: sqlite3.Connection, import_id: int
) -> list[AttendanceShiftInput]:
    rows = conn.execute(
        "SELECT * FROM attendance_shifts WHERE import_id = ? "
        "ORDER BY work_date, employee_code",
        (import_id,),
    ).fetchall()
    return [
        AttendanceShiftInput(
            employee_code=row["employee_code"],
            work_date=row["work_date"],
            raw_shift=row["raw_shift"],
            shift_type=row["shift_type"],
            available_for_cleaning=bool(row["available_for_cleaning"]),
            staff_id=row["staff_id"],
            employee_name=row["employee_name"],
            department=row["department"],
            start_minutes=row["start_minutes"],
            end_minutes=row["end_minutes"],
        )
        for row in rows
    ]
