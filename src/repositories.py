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
    SpecialSkill,
    StaffDatePreferenceInput,
    StaffDetail,
    StaffInput,
)
from src.month_utils import get_month_dates
from src.work_time import normalize_hhmm


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
        standard_start_time=row["standard_start_time"],
        standard_end_time=row["standard_end_time"],
        target_days_per_week=row["target_days_per_week"],
        max_days_per_period=row["max_days_per_period"],
        max_consecutive_days=row["max_consecutive_days"],
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


def _optional_time(value: str | None) -> str | None:
    """通常勤務時刻を 'HH:MM'（0埋め）へ正規化する. 未入力はNone.

    形式が不正な値はそのまま渡してDBのCHECKで弾く（黙って捨てない）。
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return normalize_hhmm(value) or value


def create_staff(
    conn: sqlite3.Connection,
    employee_code: str,
    staff_name: str,
    role_id: int,
    skill_level: int = SKILL_LEVEL_DEFAULT,
    department: str | None = None,
    *,
    standard_start_time: str | None = None,
    standard_end_time: str | None = None,
    target_days_per_week: int | None = None,
    max_days_per_period: int | None = None,
    max_consecutive_days: int | None = None,
    weekdays: list[int] | tuple[int, ...] | None = None,
    special_skill_ids: list[int] | tuple[int, ...] | None = None,
) -> int:
    """スタッフを作成する. employee_code 重複時は sqlite3.IntegrityError.

    staff・通常勤務曜日・特殊スキルを1トランザクションで保存する
    （途中で失敗した場合はスタッフ本体も作られない）。
    """
    with conn:
        cur = conn.execute(
            "INSERT INTO staff (employee_code, staff_name, department, role_id, skill_level, "
            "standard_start_time, standard_end_time, target_days_per_week, "
            "max_days_per_period, max_consecutive_days) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                employee_code,
                staff_name,
                department,
                role_id,
                skill_level,
                _optional_time(standard_start_time),
                _optional_time(standard_end_time),
                target_days_per_week,
                max_days_per_period,
                max_consecutive_days,
            ),
        )
        staff_id = cur.lastrowid
        _replace_staff_weekdays(conn, staff_id, weekdays)
        _replace_staff_special_skills(conn, staff_id, special_skill_ids)
    return staff_id


def update_staff(
    conn: sqlite3.Connection,
    staff_id: int,
    *,
    employee_code: str,
    staff_name: str,
    role_id: int,
    skill_level: int,
    department: str | None = None,
    standard_start_time: str | None = None,
    standard_end_time: str | None = None,
    target_days_per_week: int | None = None,
    max_days_per_period: int | None = None,
    max_consecutive_days: int | None = None,
    weekdays: list[int] | tuple[int, ...] | None = None,
    special_skill_ids: list[int] | tuple[int, ...] | None = None,
) -> None:
    """スタッフを更新する.

    weekdays / special_skill_ids は None のとき変更しない（渡された場合は全置換）。
    staff本体と関連テーブルを1トランザクションで更新するため、部分更新は起きない。
    """
    with conn:
        cur = conn.execute(
            "UPDATE staff SET employee_code = ?, staff_name = ?, department = ?, "
            "role_id = ?, skill_level = ?, standard_start_time = ?, standard_end_time = ?, "
            "target_days_per_week = ?, max_days_per_period = ?, max_consecutive_days = ? "
            "WHERE staff_id = ?",
            (
                employee_code,
                staff_name,
                department,
                role_id,
                skill_level,
                _optional_time(standard_start_time),
                _optional_time(standard_end_time),
                target_days_per_week,
                max_days_per_period,
                max_consecutive_days,
                staff_id,
            ),
        )
        if cur.rowcount == 0:
            raise ValueError(f"staff not found: {staff_id!r}")
        _replace_staff_weekdays(conn, staff_id, weekdays)
        _replace_staff_special_skills(conn, staff_id, special_skill_ids)


def deactivate_staff(conn: sqlite3.Connection, staff_id: int) -> None:
    """無効化（物理削除はしない）. 通常勤務曜日・特殊スキルの割当はそのまま残す."""
    with conn:
        cur = conn.execute(
            "UPDATE staff SET active = 0 WHERE staff_id = ?", (staff_id,)
        )
        if cur.rowcount == 0:
            raise ValueError(f"staff not found: {staff_id!r}")


# ---------------------------------------------------------------------------
# 通常勤務曜日 / 特殊スキル
# ---------------------------------------------------------------------------


def _replace_staff_weekdays(
    conn: sqlite3.Connection,
    staff_id: int,
    weekdays: list[int] | tuple[int, ...] | None,
) -> None:
    """通常勤務曜日を全置換する（Noneなら変更しない）. 呼び出し側のトランザクション内で使う."""
    if weekdays is None:
        return
    conn.execute("DELETE FROM staff_weekday_patterns WHERE staff_id = ?", (staff_id,))
    conn.executemany(
        "INSERT INTO staff_weekday_patterns (staff_id, weekday, is_available) VALUES (?, ?, 1)",
        [(staff_id, int(weekday)) for weekday in sorted(set(weekdays))],
    )


def _replace_staff_special_skills(
    conn: sqlite3.Connection,
    staff_id: int,
    special_skill_ids: list[int] | tuple[int, ...] | None,
) -> None:
    """特殊スキルの割当を全置換する（Noneなら変更しない）. 呼び出し側のトランザクション内で使う."""
    if special_skill_ids is None:
        return
    conn.execute("DELETE FROM staff_special_skills WHERE staff_id = ?", (staff_id,))
    conn.executemany(
        "INSERT INTO staff_special_skills (staff_id, special_skill_id) VALUES (?, ?)",
        [(staff_id, int(skill_id)) for skill_id in sorted(set(special_skill_ids))],
    )


def get_staff_weekdays(conn: sqlite3.Connection, staff_id: int) -> tuple[int, ...]:
    """通常勤務する曜日（0=月〜6=日）を昇順で返す."""
    rows = conn.execute(
        "SELECT weekday FROM staff_weekday_patterns "
        "WHERE staff_id = ? AND is_available = 1 ORDER BY weekday",
        (staff_id,),
    ).fetchall()
    return tuple(row["weekday"] for row in rows)


def get_staff_special_skill_ids(conn: sqlite3.Connection, staff_id: int) -> tuple[int, ...]:
    rows = conn.execute(
        "SELECT special_skill_id FROM staff_special_skills WHERE staff_id = ? "
        "ORDER BY special_skill_id",
        (staff_id,),
    ).fetchall()
    return tuple(row["special_skill_id"] for row in rows)


def _row_to_special_skill(row: sqlite3.Row) -> SpecialSkill:
    return SpecialSkill(
        special_skill_id=row["special_skill_id"],
        skill_code=row["skill_code"],
        skill_name=row["skill_name"],
        active=bool(row["active"]),
        display_order=row["display_order"],
    )


def list_special_skills(
    conn: sqlite3.Connection, include_inactive: bool = True
) -> list[SpecialSkill]:
    """特殊スキルのマスターを表示順で返す."""
    sql = "SELECT * FROM special_skills"
    if not include_inactive:
        sql += " WHERE active = 1"
    sql += " ORDER BY display_order, special_skill_id"
    return [_row_to_special_skill(row) for row in conn.execute(sql).fetchall()]


def get_staff_detail(conn: sqlite3.Connection, staff_id: int) -> StaffDetail | None:
    """スタッフ1名の通常勤務条件（曜日・特殊スキル込み）を取得する."""
    staff = get_staff(conn, staff_id)
    if staff is None:
        return None
    return StaffDetail(
        staff=staff,
        weekdays=get_staff_weekdays(conn, staff_id),
        special_skill_ids=get_staff_special_skill_ids(conn, staff_id),
    )


def list_staff_details(
    conn: sqlite3.Connection, include_inactive: bool = True
) -> list[StaffDetail]:
    """一覧表示用. 曜日・特殊スキルは全件まとめて取得する（1名ずつ問い合わせない）."""
    staff_list = list_staff(conn, include_inactive=include_inactive)
    if not staff_list:
        return []

    weekdays: dict[int, list[int]] = {}
    for row in conn.execute(
        "SELECT staff_id, weekday FROM staff_weekday_patterns "
        "WHERE is_available = 1 ORDER BY staff_id, weekday"
    ):
        weekdays.setdefault(row["staff_id"], []).append(row["weekday"])

    skills: dict[int, list[int]] = {}
    for row in conn.execute(
        "SELECT staff_id, special_skill_id FROM staff_special_skills "
        "ORDER BY staff_id, special_skill_id"
    ):
        skills.setdefault(row["staff_id"], []).append(row["special_skill_id"])

    return [
        StaffDetail(
            staff=staff,
            weekdays=tuple(weekdays.get(staff.staff_id, ())),
            special_skill_ids=tuple(skills.get(staff.staff_id, ())),
        )
        for staff in staff_list
    ]


# ---------------------------------------------------------------------------
# 期間別勤務希望（行がない = 通常）
# ---------------------------------------------------------------------------


def _row_to_preference(row: sqlite3.Row) -> StaffDatePreferenceInput:
    return StaffDatePreferenceInput(
        staff_id=row["staff_id"],
        work_date=row["work_date"],
        absolute_off=bool(row["absolute_off"]),
        prefer_off=bool(row["prefer_off"]),
        available_extra=bool(row["available_extra"]),
        override_start_time=row["override_start_time"],
        override_end_time=row["override_end_time"],
        note=row["note"],
    )


def get_staff_date_preference(
    conn: sqlite3.Connection, staff_id: int, work_date: str
) -> StaffDatePreferenceInput | None:
    """1日分の勤務希望. 行がなければNone（= 通常条件を使う）."""
    row = conn.execute(
        "SELECT * FROM staff_date_preferences WHERE staff_id = ? AND work_date = ?",
        (staff_id, work_date),
    ).fetchone()
    return None if row is None else _row_to_preference(row)


def list_staff_preferences(
    conn: sqlite3.Connection, staff_id: int, start_date: str, end_date: str
) -> list[StaffDatePreferenceInput]:
    """1スタッフの期間内の勤務希望を日付昇順で返す（存在する行のみ）."""
    rows = conn.execute(
        "SELECT * FROM staff_date_preferences "
        "WHERE staff_id = ? AND work_date BETWEEN ? AND ? ORDER BY work_date",
        (staff_id, start_date, end_date),
    ).fetchall()
    return [_row_to_preference(row) for row in rows]


def list_preferences_in_period(
    conn: sqlite3.Connection, start_date: str, end_date: str
) -> list[StaffDatePreferenceInput]:
    """期間内の全スタッフの勤務希望（スタッフ×日付の一覧表示用）."""
    rows = conn.execute(
        "SELECT * FROM staff_date_preferences WHERE work_date BETWEEN ? AND ? "
        "ORDER BY staff_id, work_date",
        (start_date, end_date),
    ).fetchall()
    return [_row_to_preference(row) for row in rows]


_UPSERT_PREFERENCE_SQL = """
    INSERT INTO staff_date_preferences (
        staff_id, work_date, absolute_off, prefer_off, available_extra,
        override_start_time, override_end_time, note, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (staff_id, work_date) DO UPDATE SET
        absolute_off = excluded.absolute_off,
        prefer_off = excluded.prefer_off,
        available_extra = excluded.available_extra,
        override_start_time = excluded.override_start_time,
        override_end_time = excluded.override_end_time,
        note = excluded.note,
        updated_at = excluded.updated_at
"""


def _preference_params(pref: StaffDatePreferenceInput, now: str) -> tuple:
    return (
        pref.staff_id,
        pref.work_date,
        int(pref.absolute_off),
        int(pref.prefer_off),
        int(pref.available_extra),
        _optional_time(pref.override_start_time),
        _optional_time(pref.override_end_time),
        _optional_note(pref.note),
        now,
        now,
    )


def _optional_note(note: str | None) -> str | None:
    if not isinstance(note, str):
        return None
    return note.strip() or None


def _upsert_preference(
    conn: sqlite3.Connection, pref: StaffDatePreferenceInput, now: str
) -> None:
    """呼び出し側のトランザクション内で使う1件分のupsert."""
    conn.execute(_UPSERT_PREFERENCE_SQL, _preference_params(pref, now))


def _delete_preference(conn: sqlite3.Connection, staff_id: int, work_date: str) -> None:
    conn.execute(
        "DELETE FROM staff_date_preferences WHERE staff_id = ? AND work_date = ?",
        (staff_id, work_date),
    )


def upsert_staff_date_preference(
    conn: sqlite3.Connection, pref: StaffDatePreferenceInput
) -> None:
    """1日分の勤務希望を保存する. すべて通常どおりの内容なら行を削除する."""
    with conn:
        if pref.is_empty:
            _delete_preference(conn, pref.staff_id, pref.work_date)
        else:
            _upsert_preference(conn, pref, _now())


def delete_staff_date_preference(
    conn: sqlite3.Connection, staff_id: int, work_date: str
) -> None:
    """その日を通常条件に戻す（行を削除する）."""
    with conn:
        _delete_preference(conn, staff_id, work_date)


def save_staff_period_preferences(
    conn: sqlite3.Connection,
    staff_id: int,
    work_dates: list[str],
    preferences: list[StaffDatePreferenceInput],
) -> None:
    """1スタッフの期間分の勤務希望を1トランザクションで保存する.

    work_dates のうち preferences に含まれない日、および内容が通常どおりの日は
    行を削除する（行がない = 通常 を維持するため）。
    途中で失敗した場合は一部の日だけ保存された状態にならない。
    """
    target_dates = set(work_dates)
    for pref in preferences:
        if pref.staff_id != staff_id:
            raise ValueError(
                f"preference staff_id {pref.staff_id!r} does not match {staff_id!r}"
            )
        if pref.work_date not in target_dates:
            raise ValueError(f"preference work_date {pref.work_date!r} is out of period")

    now = _now()
    keep = {p.work_date: p for p in preferences if not p.is_empty}

    with conn:
        for work_date in work_dates:
            pref = keep.get(work_date)
            if pref is None:
                _delete_preference(conn, staff_id, work_date)
            else:
                _upsert_preference(conn, pref, now)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def list_active_imports(conn: sqlite3.Connection) -> list[AttendanceImportRecord]:
    """全月のACTIVE取込を対象月の新しい順で返す."""
    rows = conn.execute(
        "SELECT * FROM attendance_imports WHERE status = ? ORDER BY year_month DESC",
        (IMPORT_STATUS_ACTIVE,),
    ).fetchall()
    return [_row_to_import_record(row) for row in rows]


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
