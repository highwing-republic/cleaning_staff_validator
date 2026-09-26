"""DBアクセス層（§17-§26, §46-§48）.

業務検証（Phase 4のvalidation）は行わない。DB制約に委ねる。
ただしScheduleConfirmedError等、本ファイル内で明示された検査のみ行う。
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from src.constants import (
    SCHEDULE_STATUS_CONFIRMED,
    SCHEDULE_STATUS_DRAFT,
    SKILL_LEVEL_DEFAULT,
    SOURCE_MANUAL,
    SOURCE_OPTIMIZED,
)
from src.models import (
    DailyRequirementInput,
    LockedAssignmentInput,
    MonthlyConditionInput,
    PreferenceInput,
    RoleRequirementInput,
    SchedulerResult,
    StaffInput,
)
from src.month_utils import get_month_dates, parse_year_month


class ScheduleConfirmedError(Exception):
    """CONFIRMED月に対する編集操作が行われた場合（§48）."""


# ---------------------------------------------------------------------------
# T18 スケジュール用のレコード型（modelsは変更しないためここで定義）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleAssignmentRecord:
    staff_id: int
    work_date: str
    is_working: bool
    is_locked: bool
    source: str


@dataclass(frozen=True)
class ScheduleMonthRecord:
    year_month: str
    status: str
    solver_status: str | None
    objective_overstaff: int | None
    objective_target_deviation: int | None
    objective_prefer_off: int | None
    objective_prefer_work: int | None
    generated_at: str | None
    confirmed_at: str | None
    objective_max_deviation: int | None = None
    objective_max_overstaff: int | None = None


# ---------------------------------------------------------------------------
# T13 staff
# ---------------------------------------------------------------------------


def list_roles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT role_id, role_code, role_name, active FROM roles ORDER BY role_id"
    ).fetchall()
    return [dict(row) for row in rows]


def _load_weekday_availability(conn: sqlite3.Connection, staff_id: int) -> dict[int, bool]:
    rows = conn.execute(
        "SELECT weekday, is_available FROM staff_weekday_availability "
        "WHERE staff_id = ? ORDER BY weekday",
        (staff_id,),
    ).fetchall()
    return {row["weekday"]: bool(row["is_available"]) for row in rows}


def _row_to_staff_input(conn: sqlite3.Connection, row: sqlite3.Row) -> StaffInput:
    return StaffInput(
        staff_id=row["staff_id"],
        staff_name=row["staff_name"],
        role_id=row["role_id"],
        daily_work_minutes=row["daily_work_minutes"],
        max_consecutive_days=row["max_consecutive_days"],
        active=bool(row["active"]),
        weekday_availability=_load_weekday_availability(conn, row["staff_id"]),
        skill_level=row["skill_level"],
    )


def list_staff(conn: sqlite3.Connection, include_inactive: bool = True) -> list[StaffInput]:
    if include_inactive:
        rows = conn.execute("SELECT * FROM staff ORDER BY staff_id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM staff WHERE active = 1 ORDER BY staff_id"
        ).fetchall()
    return [_row_to_staff_input(conn, row) for row in rows]


def get_staff(conn: sqlite3.Connection, staff_id: int) -> StaffInput | None:
    row = conn.execute(
        "SELECT * FROM staff WHERE staff_id = ?", (staff_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_staff_input(conn, row)


def create_staff(
    conn: sqlite3.Connection,
    staff_name: str,
    role_id: int,
    daily_work_minutes: int,
    max_consecutive_days: int,
    skill_level: int = SKILL_LEVEL_DEFAULT,
) -> int:
    """スタッフを作成し、7曜日すべてavailableの行も同時に作る（§20, T08）."""
    with conn:
        cur = conn.execute(
            "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
            "max_consecutive_days, skill_level) VALUES (?, ?, ?, ?, ?)",
            (staff_name, role_id, daily_work_minutes, max_consecutive_days, skill_level),
        )
        staff_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO staff_weekday_availability (staff_id, weekday, "
            "is_available) VALUES (?, ?, 1)",
            [(staff_id, weekday) for weekday in range(7)],
        )
    return staff_id


def update_staff(
    conn: sqlite3.Connection,
    staff_id: int,
    *,
    staff_name: str,
    role_id: int,
    daily_work_minutes: int,
    max_consecutive_days: int,
    skill_level: int,
) -> None:
    with conn:
        cur = conn.execute(
            "UPDATE staff SET staff_name = ?, role_id = ?, daily_work_minutes = ?, "
            "max_consecutive_days = ?, skill_level = ? WHERE staff_id = ?",
            (staff_name, role_id, daily_work_minutes, max_consecutive_days, skill_level, staff_id),
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
# T14 weekday availability
# ---------------------------------------------------------------------------


def get_weekday_availability(conn: sqlite3.Connection, staff_id: int) -> dict[int, bool]:
    return _load_weekday_availability(conn, staff_id)


def save_weekday_availability(
    conn: sqlite3.Connection, staff_id: int, availability: dict[int, bool]
) -> None:
    if set(availability.keys()) != set(range(7)):
        raise ValueError(f"availability must have exactly keys 0..6: {sorted(availability.keys())!r}")
    with conn:
        conn.execute(
            "DELETE FROM staff_weekday_availability WHERE staff_id = ?", (staff_id,)
        )
        conn.executemany(
            "INSERT INTO staff_weekday_availability (staff_id, weekday, "
            "is_available) VALUES (?, ?, ?)",
            [(staff_id, weekday, int(bool(is_avail))) for weekday, is_avail in availability.items()],
        )


# ---------------------------------------------------------------------------
# T15 monthly conditions
# ---------------------------------------------------------------------------


def _row_to_monthly_condition(row: sqlite3.Row) -> MonthlyConditionInput:
    return MonthlyConditionInput(
        staff_id=row["staff_id"],
        year_month=row["year_month"],
        target_monthly_minutes=row["target_monthly_minutes"],
        min_monthly_minutes=row["min_monthly_minutes"],
        max_monthly_minutes=row["max_monthly_minutes"],
        carryover_consecutive_days=row["carryover_consecutive_days"],
    )


def get_monthly_conditions(
    conn: sqlite3.Connection, year_month: str
) -> list[MonthlyConditionInput]:
    parse_year_month(year_month)
    rows = conn.execute(
        "SELECT * FROM staff_monthly_conditions WHERE year_month = ? "
        "ORDER BY staff_id",
        (year_month,),
    ).fetchall()
    return [_row_to_monthly_condition(row) for row in rows]


def get_monthly_condition(
    conn: sqlite3.Connection, staff_id: int, year_month: str
) -> MonthlyConditionInput | None:
    parse_year_month(year_month)
    row = conn.execute(
        "SELECT * FROM staff_monthly_conditions WHERE staff_id = ? AND year_month = ?",
        (staff_id, year_month),
    ).fetchone()
    if row is None:
        return None
    return _row_to_monthly_condition(row)


def save_monthly_condition(conn: sqlite3.Connection, cond: MonthlyConditionInput) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO staff_monthly_conditions (
                staff_id, year_month, target_monthly_minutes,
                min_monthly_minutes, max_monthly_minutes, carryover_consecutive_days
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (staff_id, year_month) DO UPDATE SET
                target_monthly_minutes = excluded.target_monthly_minutes,
                min_monthly_minutes = excluded.min_monthly_minutes,
                max_monthly_minutes = excluded.max_monthly_minutes,
                carryover_consecutive_days = excluded.carryover_consecutive_days
            """,
            (
                cond.staff_id,
                cond.year_month,
                cond.target_monthly_minutes,
                cond.min_monthly_minutes,
                cond.max_monthly_minutes,
                cond.carryover_consecutive_days,
            ),
        )


# ---------------------------------------------------------------------------
# T16 preferences
# ---------------------------------------------------------------------------


def get_preferences(conn: sqlite3.Connection, year_month: str) -> list[PreferenceInput]:
    dates = get_month_dates(year_month)
    first, last = dates[0], dates[-1]
    rows = conn.execute(
        "SELECT staff_id, work_date, preference_type FROM staff_day_preferences "
        "WHERE work_date BETWEEN ? AND ? ORDER BY staff_id, work_date",
        (first, last),
    ).fetchall()
    return [
        PreferenceInput(row["staff_id"], row["work_date"], row["preference_type"])
        for row in rows
    ]


def save_preference(conn: sqlite3.Connection, pref: PreferenceInput) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO staff_day_preferences (staff_id, work_date, preference_type)
            VALUES (?, ?, ?)
            ON CONFLICT (staff_id, work_date) DO UPDATE SET
                preference_type = excluded.preference_type
            """,
            (pref.staff_id, pref.work_date, pref.preference_type),
        )


def delete_preference(conn: sqlite3.Connection, staff_id: int, work_date: str) -> None:
    with conn:
        conn.execute(
            "DELETE FROM staff_day_preferences WHERE staff_id = ? AND work_date = ?",
            (staff_id, work_date),
        )


# ---------------------------------------------------------------------------
# T17 requirements
# ---------------------------------------------------------------------------


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
        )
        for row in rows
    ]


def save_daily_requirement(conn: sqlite3.Connection, req: DailyRequirementInput) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO daily_requirements (
                work_date, occupancy_rate, required_total_staff, max_total_staff, note
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (work_date) DO UPDATE SET
                occupancy_rate = excluded.occupancy_rate,
                required_total_staff = excluded.required_total_staff,
                max_total_staff = excluded.max_total_staff,
                note = excluded.note
            """,
            (req.work_date, req.occupancy_rate, req.required_total_staff, req.max_total_staff, req.note),
        )


def save_daily_requirements(
    conn: sqlite3.Connection, reqs: list[DailyRequirementInput]
) -> None:
    """全件成功時のみ保存する一括upsert（T70で使用）."""
    with conn:
        for req in reqs:
            conn.execute(
                """
                INSERT INTO daily_requirements (
                    work_date, occupancy_rate, required_total_staff, max_total_staff, note
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (work_date) DO UPDATE SET
                    occupancy_rate = excluded.occupancy_rate,
                    required_total_staff = excluded.required_total_staff,
                    max_total_staff = excluded.max_total_staff,
                    note = excluded.note
                """,
                (req.work_date, req.occupancy_rate, req.required_total_staff, req.max_total_staff, req.note),
            )


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
# T18 schedule
# ---------------------------------------------------------------------------


def get_schedule_month(conn: sqlite3.Connection, year_month: str) -> ScheduleMonthRecord | None:
    row = conn.execute(
        "SELECT * FROM schedule_months WHERE year_month = ?", (year_month,)
    ).fetchone()
    if row is None:
        return None
    return ScheduleMonthRecord(
        year_month=row["year_month"],
        status=row["status"],
        solver_status=row["solver_status"],
        objective_overstaff=row["objective_overstaff"],
        objective_target_deviation=row["objective_target_deviation"],
        objective_prefer_off=row["objective_prefer_off"],
        objective_prefer_work=row["objective_prefer_work"],
        generated_at=row["generated_at"],
        confirmed_at=row["confirmed_at"],
        objective_max_deviation=row["objective_max_deviation"],
        objective_max_overstaff=row["objective_max_overstaff"],
    )


def is_month_confirmed(conn: sqlite3.Connection, year_month: str) -> bool:
    row = conn.execute(
        "SELECT status FROM schedule_months WHERE year_month = ?", (year_month,)
    ).fetchone()
    return row is not None and row["status"] == SCHEDULE_STATUS_CONFIRMED


def _ensure_not_confirmed(conn: sqlite3.Connection, year_month: str) -> None:
    if is_month_confirmed(conn, year_month):
        raise ScheduleConfirmedError(f"schedule month is CONFIRMED: {year_month!r}")


def load_assignments(conn: sqlite3.Connection, year_month: str) -> list[ScheduleAssignmentRecord]:
    dates = get_month_dates(year_month)
    first, last = dates[0], dates[-1]
    rows = conn.execute(
        "SELECT * FROM schedule_assignments WHERE work_date BETWEEN ? AND ? "
        "ORDER BY staff_id, work_date",
        (first, last),
    ).fetchall()
    return [
        ScheduleAssignmentRecord(
            staff_id=row["staff_id"],
            work_date=row["work_date"],
            is_working=bool(row["is_working"]),
            is_locked=bool(row["is_locked"]),
            source=row["source"],
        )
        for row in rows
    ]


def get_locked_assignments(conn: sqlite3.Connection, year_month: str) -> list[LockedAssignmentInput]:
    dates = get_month_dates(year_month)
    first, last = dates[0], dates[-1]
    rows = conn.execute(
        "SELECT staff_id, work_date, is_working FROM schedule_assignments "
        "WHERE is_locked = 1 AND work_date BETWEEN ? AND ? "
        "ORDER BY staff_id, work_date",
        (first, last),
    ).fetchall()
    return [
        LockedAssignmentInput(row["staff_id"], row["work_date"], bool(row["is_working"]))
        for row in rows
    ]


def save_generated_schedule(
    conn: sqlite3.Connection, year_month: str, result: SchedulerResult
) -> None:
    """生成結果を保存する（§46-§48, T74）.

    - result.has_solution が False（INFEASIBLE/UNKNOWN）なら ValueError、既存データは変更しない。
    - CONFIRMED月なら ScheduleConfirmedError。
    - locked行は維持し、unlocked行のみ削除して結果を挿入（source=OPTIMIZED, is_locked=0）。
    """
    if not result.has_solution:
        raise ValueError(f"cannot save schedule without a solution: status={result.status!r}")

    _ensure_not_confirmed(conn, year_month)

    valid_dates = set(get_month_dates(year_month))
    for assignment in result.assignments:
        if assignment.work_date not in valid_dates:
            raise ValueError(
                f"assignment work_date {assignment.work_date!r} is not in {year_month!r}"
            )

    generated_at = datetime.now().isoformat(timespec="seconds")

    with conn:
        first, last = min(valid_dates), max(valid_dates)
        locked_rows = conn.execute(
            "SELECT staff_id, work_date FROM schedule_assignments "
            "WHERE is_locked = 1 AND work_date BETWEEN ? AND ?",
            (first, last),
        ).fetchall()
        locked_keys = {(row["staff_id"], row["work_date"]) for row in locked_rows}

        conn.execute(
            "DELETE FROM schedule_assignments WHERE is_locked = 0 "
            "AND work_date BETWEEN ? AND ?",
            (first, last),
        )
        conn.executemany(
            "INSERT INTO schedule_assignments (staff_id, work_date, is_working, "
            "is_locked, source) VALUES (?, ?, ?, 0, ?)",
            [
                (a.staff_id, a.work_date, int(a.is_working), SOURCE_OPTIMIZED)
                for a in result.assignments
                if (a.staff_id, a.work_date) not in locked_keys
            ],
        )
        conn.execute(
            """
            INSERT INTO schedule_months (
                year_month, status, solver_status,
                objective_overstaff, objective_target_deviation,
                objective_prefer_off, objective_prefer_work,
                objective_max_deviation, objective_max_overstaff,
                generated_at, confirmed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT (year_month) DO UPDATE SET
                status = excluded.status,
                solver_status = excluded.solver_status,
                objective_overstaff = excluded.objective_overstaff,
                objective_target_deviation = excluded.objective_target_deviation,
                objective_prefer_off = excluded.objective_prefer_off,
                objective_prefer_work = excluded.objective_prefer_work,
                objective_max_deviation = excluded.objective_max_deviation,
                objective_max_overstaff = excluded.objective_max_overstaff,
                generated_at = excluded.generated_at,
                confirmed_at = NULL
            """,
            (
                year_month,
                SCHEDULE_STATUS_DRAFT,
                result.status,
                result.objective_overstaff,
                result.objective_target_deviation,
                result.objective_prefer_off,
                result.objective_prefer_work,
                result.objective_max_deviation,
                result.objective_max_overstaff,
                generated_at,
            ),
        )


def update_assignment_manual(
    conn: sqlite3.Connection, staff_id: int, work_date: str, is_working: bool
) -> None:
    year_month = work_date[:7]
    _ensure_not_confirmed(conn, year_month)
    with conn:
        existing = conn.execute(
            "SELECT is_locked FROM schedule_assignments WHERE staff_id = ? AND work_date = ?",
            (staff_id, work_date),
        ).fetchone()
        is_locked = existing["is_locked"] if existing is not None else 0
        conn.execute(
            """
            INSERT INTO schedule_assignments (staff_id, work_date, is_working, is_locked, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (staff_id, work_date) DO UPDATE SET
                is_working = excluded.is_working,
                source = excluded.source
            """,
            (staff_id, work_date, int(is_working), is_locked, SOURCE_MANUAL),
        )


def set_lock(conn: sqlite3.Connection, staff_id: int, work_date: str, is_locked: bool) -> None:
    year_month = work_date[:7]
    _ensure_not_confirmed(conn, year_month)
    with conn:
        cur = conn.execute(
            "UPDATE schedule_assignments SET is_locked = ? WHERE staff_id = ? AND work_date = ?",
            (int(is_locked), staff_id, work_date),
        )
        if cur.rowcount == 0:
            raise ValueError(
                f"assignment not found: staff_id={staff_id!r}, work_date={work_date!r}"
            )


def confirm_schedule_month(conn: sqlite3.Connection, year_month: str) -> None:
    with conn:
        row = conn.execute(
            "SELECT status FROM schedule_months WHERE year_month = ?", (year_month,)
        ).fetchone()
        if row is None:
            raise ValueError(f"schedule month not found: {year_month!r}")
        if row["status"] == SCHEDULE_STATUS_CONFIRMED:
            raise ScheduleConfirmedError(f"schedule month already CONFIRMED: {year_month!r}")
        confirmed_at = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "UPDATE schedule_months SET status = ?, confirmed_at = ? WHERE year_month = ?",
            (SCHEDULE_STATUS_CONFIRMED, confirmed_at, year_month),
        )


def unconfirm_schedule_month(conn: sqlite3.Connection, year_month: str) -> None:
    """確定を解除して DRAFT に戻す（計画書 v1.5 §48）. 勤務データ・固定は変更しない."""
    with conn:
        row = conn.execute(
            "SELECT status FROM schedule_months WHERE year_month = ?", (year_month,)
        ).fetchone()
        if row is None:
            raise ValueError(f"schedule month not found: {year_month!r}")
        if row["status"] != SCHEDULE_STATUS_CONFIRMED:
            raise ValueError(f"schedule month is not CONFIRMED: {year_month!r}")
        conn.execute(
            "UPDATE schedule_months SET status = ?, confirmed_at = NULL WHERE year_month = ?",
            (SCHEDULE_STATUS_DRAFT, year_month),
        )
