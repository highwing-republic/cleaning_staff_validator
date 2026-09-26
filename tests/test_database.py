import sqlite3

import pytest

from src.database import IncompatibleSchemaError, get_connection, initialize_database

EXPECTED_TABLES = {
    "roles",
    "staff",
    "daily_requirements",
    "daily_role_requirements",
    "attendance_imports",
    "attendance_shifts",
}

REMOVED_TABLES = {
    "staff_weekday_availability",
    "staff_monthly_conditions",
    "staff_day_preferences",
    "schedule_months",
    "schedule_assignments",
}


@pytest.fixture()
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


def _columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _tables(conn):
    return {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _insert_staff(conn, employee_code="0001", role_id=1):
    with conn:
        cur = conn.execute(
            "INSERT INTO staff (employee_code, staff_name, role_id) VALUES (?, 'a', ?)",
            (employee_code, role_id),
        )
    return cur.lastrowid


def _insert_import(conn, year_month="2026-09", status="ACTIVE"):
    with conn:
        cur = conn.execute(
            "INSERT INTO attendance_imports (year_month, source_filename, imported_at, "
            "employee_count, shift_count, unmatched_count, status) "
            "VALUES (?, 'a.csv', '2026-09-26T10:00:00', 0, 0, 0, ?)",
            (year_month, status),
        )
    return cur.lastrowid


def _insert_shift(conn, import_id, **kw):
    values = dict(
        import_id=import_id,
        staff_id=None,
        employee_code="0001",
        work_date="2026-09-01",
        raw_shift="09:00-15:30",
        shift_type="TIME_RANGE",
        start_minutes=540,
        end_minutes=930,
        available_for_cleaning=1,
    )
    values.update(kw)
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    with conn:
        conn.execute(
            f"INSERT INTO attendance_shifts ({cols}) VALUES ({marks})", tuple(values.values())
        )


# ---------------------------------------------------------------------------
# tables / columns
# ---------------------------------------------------------------------------


def test_all_tables_created(conn):
    tables = _tables(conn)
    assert EXPECTED_TABLES <= tables
    assert not REMOVED_TABLES & tables


def test_staff_columns(conn):
    assert _columns(conn, "staff") == [
        "staff_id",
        "employee_code",
        "staff_name",
        "department",
        "role_id",
        "skill_level",
        "active",
    ]


def test_employee_code_is_text(conn):
    types = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(staff)")}
    assert types["employee_code"] == "TEXT"
    _insert_staff(conn, "0015")
    assert conn.execute("SELECT employee_code FROM staff").fetchone()[0] == "0015"


def test_daily_requirements_columns(conn):
    assert _columns(conn, "daily_requirements") == [
        "work_date",
        "occupancy_rate",
        "required_total_staff",
        "max_total_staff",
        "note",
        "required_skill_level",
        "required_skill_count",
    ]


def test_daily_role_requirements_columns(conn):
    assert _columns(conn, "daily_role_requirements") == [
        "work_date",
        "role_id",
        "required_count",
    ]


def test_attendance_imports_columns(conn):
    assert _columns(conn, "attendance_imports") == [
        "import_id",
        "year_month",
        "source_filename",
        "imported_at",
        "employee_count",
        "shift_count",
        "unmatched_count",
        "status",
    ]


def test_attendance_shifts_columns(conn):
    assert _columns(conn, "attendance_shifts") == [
        "shift_id",
        "import_id",
        "staff_id",
        "employee_code",
        "employee_name",
        "department",
        "work_date",
        "raw_shift",
        "shift_type",
        "start_minutes",
        "end_minutes",
        "available_for_cleaning",
    ]


# ---------------------------------------------------------------------------
# init / roles
# ---------------------------------------------------------------------------


def test_initialize_is_idempotent(conn):
    initialize_database(conn)
    rows = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
    assert rows == 3


def test_roles_seeded(conn):
    rows = conn.execute(
        "SELECT role_id, role_code, role_name FROM roles ORDER BY role_id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        (1, "LEADER", "リーダー"),
        (2, "CHECKER", "チェッカー"),
        (3, "CLEANER", "クリーナー"),
    ]


def test_get_connection_memory_row_factory():
    conn = get_connection(":memory:")
    initialize_database(conn)
    row = conn.execute("SELECT role_id, role_code FROM roles WHERE role_id=1").fetchone()
    assert row["role_code"] == "LEADER"
    conn.close()


def test_get_connection_tmp_path(tmp_path):
    db_path = tmp_path / "sub" / "app.db"
    conn = get_connection(db_path)
    initialize_database(conn)
    assert db_path.exists()
    conn.close()


# ---------------------------------------------------------------------------
# staff constraints
# ---------------------------------------------------------------------------


def test_staff_foreign_key_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_staff(conn, role_id=999)


def test_employee_code_unique(conn):
    _insert_staff(conn, "0001")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_staff(conn, "0001")


@pytest.mark.parametrize("code", [None, "", "   "])
def test_employee_code_required(conn, code):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_staff(conn, code)


@pytest.mark.parametrize("skill_level", [0, 6])
def test_check_skill_level_range(conn, skill_level):
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO staff (employee_code, staff_name, role_id, skill_level) "
                "VALUES ('1', 'a', 1, ?)",
                (skill_level,),
            )


def test_staff_defaults(conn):
    _insert_staff(conn)
    row = conn.execute("SELECT skill_level, active, department FROM staff").fetchone()
    assert (row["skill_level"], row["active"], row["department"]) == (3, 1, None)


# ---------------------------------------------------------------------------
# daily_requirements constraints
# ---------------------------------------------------------------------------


def _insert_requirement(conn, level, count):
    with conn:
        conn.execute(
            "INSERT INTO daily_requirements (work_date, required_total_staff, "
            "required_skill_level, required_skill_count) VALUES ('2026-10-01', 5, ?, ?)",
            (level, count),
        )


def test_requirement_skill_defaults_and_nulls(conn):
    with conn:
        conn.execute(
            "INSERT INTO daily_requirements (work_date, required_total_staff) "
            "VALUES ('2026-10-01', 5)"
        )
    row = conn.execute(
        "SELECT max_total_staff, occupancy_rate, note, required_skill_level, "
        "required_skill_count FROM daily_requirements"
    ).fetchone()
    assert tuple(row) == (None, None, None, None, 0)


@pytest.mark.parametrize("level,count", [(4, 2), (None, 0), (1, 0), (5, 1)])
def test_requirement_skill_valid(conn, level, count):
    _insert_requirement(conn, level, count)


@pytest.mark.parametrize("level,count", [(0, 1), (6, 1), (4, -1), (None, 1)])
def test_requirement_skill_invalid(conn, level, count):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_requirement(conn, level, count)


def test_role_requirement_foreign_key_and_primary_key(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO daily_role_requirements VALUES ('2026-10-01', 999, 1)"
            )
    with conn:
        conn.execute("INSERT INTO daily_role_requirements VALUES ('2026-10-01', 1, 1)")
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("INSERT INTO daily_role_requirements VALUES ('2026-10-01', 1, 2)")


# ---------------------------------------------------------------------------
# attendance constraints
# ---------------------------------------------------------------------------


def test_import_status_check(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_import(conn, status="DRAFT")


def test_only_one_active_import_per_month(conn):
    _insert_import(conn, "2026-09", "ACTIVE")
    _insert_import(conn, "2026-09", "SUPERSEDED")
    _insert_import(conn, "2026-09", "SUPERSEDED")
    _insert_import(conn, "2026-10", "ACTIVE")  # 別月はOK
    with pytest.raises(sqlite3.IntegrityError):
        _insert_import(conn, "2026-09", "ACTIVE")


def test_shift_unmatched_staff_null_allowed(conn):
    import_id = _insert_import(conn)
    _insert_shift(conn, import_id, staff_id=None, employee_code="9999")
    row = conn.execute("SELECT staff_id, employee_code FROM attendance_shifts").fetchone()
    assert tuple(row) == (None, "9999")


def test_shift_staff_foreign_key(conn):
    import_id = _insert_import(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_shift(conn, import_id, staff_id=999)


def test_shift_import_foreign_key(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_shift(conn, 999)


def test_shift_unique_per_import_employee_date(conn):
    import_id = _insert_import(conn)
    _insert_shift(conn, import_id)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_shift(conn, import_id)


def test_shift_type_check(conn):
    import_id = _insert_import(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_shift(conn, import_id, shift_type="HOLIDAY", start_minutes=None,
                      end_minutes=None, available_for_cleaning=0)


def test_shift_time_range_allows_over_24h(conn):
    import_id = _insert_import(conn)
    # 23:00-30:00（翌6:00相当）
    _insert_shift(conn, import_id, raw_shift="23:00-30:00", start_minutes=1380, end_minutes=1800)


@pytest.mark.parametrize(
    "shift_type,start,end",
    [
        ("TIME_RANGE", None, None),  # TIME_RANGEは時刻必須
        ("TIME_RANGE", 540, None),
        ("UNKNOWN", 540, 930),  # TIME_RANGE以外は時刻なし
    ],
)
def test_shift_minutes_consistent_with_type(conn, shift_type, start, end):
    import_id = _insert_import(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_shift(conn, import_id, shift_type=shift_type, start_minutes=start,
                      end_minutes=end, available_for_cleaning=0)


@pytest.mark.parametrize("shift_type", ["OTHER_DUTY", "BLANK", "UNKNOWN"])
def test_only_time_range_can_be_available_for_cleaning(conn, shift_type):
    import_id = _insert_import(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_shift(conn, import_id, shift_type=shift_type, start_minutes=None,
                      end_minutes=None, available_for_cleaning=1)


def test_shift_keeps_raw_values(conn):
    import_id = _insert_import(conn)
    _insert_shift(conn, import_id, raw_shift="深夜フロント（夜勤）", shift_type="OTHER_DUTY",
                  start_minutes=None, end_minutes=None, available_for_cleaning=0)
    _insert_shift(conn, import_id, work_date="2026-09-02", raw_shift="",
                  shift_type="BLANK", start_minutes=None, end_minutes=None,
                  available_for_cleaning=0)
    rows = conn.execute(
        "SELECT raw_shift, shift_type FROM attendance_shifts ORDER BY work_date"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("深夜フロント（夜勤）", "OTHER_DUTY"), ("", "BLANK")]


# ---------------------------------------------------------------------------
# 元アプリDBへの誤接続防止
# ---------------------------------------------------------------------------


def test_initialize_rejects_source_app_schema():
    conn = get_connection(":memory:")
    with conn:
        conn.execute(
            "CREATE TABLE staff (staff_id INTEGER PRIMARY KEY, staff_name TEXT NOT NULL, "
            "role_id INTEGER NOT NULL, daily_work_minutes INTEGER NOT NULL, "
            "max_consecutive_days INTEGER NOT NULL)"
        )
    with pytest.raises(IncompatibleSchemaError):
        initialize_database(conn)
    # 既存の旧テーブルに手を加えていないこと
    assert "attendance_imports" not in _tables(conn)
    conn.close()
