import sqlite3

import pytest

from src.database import get_connection, initialize_database

EXPECTED_TABLES = {
    "roles",
    "staff",
    "staff_weekday_availability",
    "staff_monthly_conditions",
    "staff_day_preferences",
    "daily_requirements",
    "daily_role_requirements",
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


def test_all_tables_created(conn):
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert EXPECTED_TABLES <= tables


def test_staff_columns(conn):
    cols = _columns(conn, "staff")
    assert cols == [
        "staff_id",
        "staff_name",
        "role_id",
        "daily_work_minutes",
        "max_consecutive_days",
        "active",
        "skill_level",
    ]


def test_weekday_availability_columns(conn):
    assert _columns(conn, "staff_weekday_availability") == [
        "staff_id",
        "weekday",
        "is_available",
    ]


def test_monthly_conditions_columns(conn):
    assert _columns(conn, "staff_monthly_conditions") == [
        "staff_id",
        "year_month",
        "target_monthly_minutes",
        "min_monthly_minutes",
        "max_monthly_minutes",
        "carryover_consecutive_days",
    ]


def test_day_preferences_columns(conn):
    assert _columns(conn, "staff_day_preferences") == [
        "staff_id",
        "work_date",
        "preference_type",
    ]


def test_daily_requirements_columns(conn):
    assert _columns(conn, "daily_requirements") == [
        "work_date",
        "occupancy_rate",
        "required_total_staff",
        "max_total_staff",
        "note",
    ]


def test_daily_role_requirements_columns(conn):
    assert _columns(conn, "daily_role_requirements") == [
        "work_date",
        "role_id",
        "required_count",
    ]


def test_schedule_months_columns(conn):
    assert _columns(conn, "schedule_months") == [
        "year_month",
        "status",
        "solver_status",
        "objective_overstaff",
        "objective_target_deviation",
        "objective_prefer_off",
        "objective_prefer_work",
        "objective_max_deviation",
        "objective_max_overstaff",
        "generated_at",
        "confirmed_at",
    ]


def test_schedule_assignments_columns(conn):
    assert _columns(conn, "schedule_assignments") == [
        "staff_id",
        "work_date",
        "is_working",
        "is_locked",
        "source",
    ]


def test_initialize_is_idempotent(conn):
    # 2回目の初期化でエラーなし、rolesは重複しない
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


def test_foreign_key_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
                "max_consecutive_days) VALUES (?, ?, ?, ?)",
                ("test", 999, 480, 5),
            )


def test_check_weekday_range(conn):
    with conn:
        conn.execute(
            "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
            "max_consecutive_days) VALUES ('a', 1, 480, 5)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO staff_weekday_availability (staff_id, weekday, "
                "is_available) VALUES (1, 7, 1)"
            )


def test_check_boolean_columns(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
                "max_consecutive_days, active) VALUES ('a', 1, 480, 5, 2)"
            )


def test_check_preference_type(conn):
    with conn:
        conn.execute(
            "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
            "max_consecutive_days) VALUES ('a', 1, 480, 5)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO staff_day_preferences (staff_id, work_date, "
                "preference_type) VALUES (1, '2026-10-01', 'HOLIDAY')"
            )


def test_check_schedule_status(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO schedule_months (year_month, status) "
                "VALUES ('2026-10', 'PENDING')"
            )


def test_check_source_type(conn):
    with conn:
        conn.execute(
            "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
            "max_consecutive_days) VALUES ('a', 1, 480, 5)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO schedule_assignments (staff_id, work_date, "
                "is_working, source) VALUES (1, '2026-10-01', 1, 'AI')"
            )


def test_primary_key_uniqueness(conn):
    with conn:
        conn.execute(
            "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
            "max_consecutive_days) VALUES ('a', 1, 480, 5)"
        )
        conn.execute(
            "INSERT INTO staff_weekday_availability (staff_id, weekday, "
            "is_available) VALUES (1, 0, 1)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO staff_weekday_availability (staff_id, weekday, "
                "is_available) VALUES (1, 0, 0)"
            )


def test_nullable_columns_accept_null(conn):
    with conn:
        conn.execute(
            "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
            "max_consecutive_days) VALUES ('a', 1, 480, 5)"
        )
        conn.execute(
            "INSERT INTO staff_monthly_conditions (staff_id, year_month, "
            "target_monthly_minutes, min_monthly_minutes, max_monthly_minutes) "
            "VALUES (1, '2026-10', 9600, NULL, NULL)"
        )
        conn.execute(
            "INSERT INTO daily_requirements (work_date, required_total_staff, "
            "max_total_staff, occupancy_rate, note) "
            "VALUES ('2026-10-01', 5, NULL, NULL, NULL)"
        )
    row = conn.execute(
        "SELECT min_monthly_minutes, max_monthly_minutes FROM "
        "staff_monthly_conditions WHERE staff_id=1"
    ).fetchone()
    assert row[0] is None and row[1] is None


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
# SK03 skill_level migration (TEST07, TEST08)
# ---------------------------------------------------------------------------


def _create_legacy_schema(conn):
    """SK03変更前のstaffテーブル（skill_level列なし）を再現する."""
    with conn:
        conn.execute(
            """
            CREATE TABLE roles (
                role_id INTEGER PRIMARY KEY,
                role_code TEXT UNIQUE NOT NULL,
                role_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE staff (
                staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_name TEXT NOT NULL,
                role_id INTEGER NOT NULL,
                daily_work_minutes INTEGER NOT NULL,
                max_consecutive_days INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                FOREIGN KEY (role_id) REFERENCES roles (role_id)
            )
            """
        )
        conn.executemany(
            "INSERT INTO roles (role_id, role_code, role_name) VALUES (?, ?, ?)",
            [(1, "LEADER", "リーダー"), (2, "CHECKER", "チェッカー"), (3, "CLEANER", "クリーナー")],
        )
        conn.executemany(
            "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
            "max_consecutive_days, active) VALUES (?, ?, ?, ?, ?)",
            [
                ("山田", 1, 480, 5, 1),
                ("鈴木", 3, 360, 4, 0),
            ],
        )


def test_migrate_adds_skill_level_default_3_to_existing_rows():
    """TEST07: 既存DB移行後、既存スタッフのskill_levelが3になる. 他の列も維持される."""
    conn = get_connection(":memory:")
    _create_legacy_schema(conn)
    assert "skill_level" not in _columns(conn, "staff")

    initialize_database(conn)

    assert "skill_level" in _columns(conn, "staff")
    rows = conn.execute(
        "SELECT staff_name, role_id, daily_work_minutes, max_consecutive_days, "
        "active, skill_level FROM staff ORDER BY staff_id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("山田", 1, 480, 5, 1, 3),
        ("鈴木", 3, 360, 4, 0, 3),
    ]
    conn.close()


def test_migrate_adds_v14_objective_columns_to_existing_schedule_months():
    """v1.4: 既存の schedule_months に目的値列を追加し、既存行は維持する（冪等）."""
    conn = get_connection(":memory:")
    conn.execute(
        """
        CREATE TABLE schedule_months (
            year_month TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            solver_status TEXT NULL,
            objective_overstaff INTEGER NULL,
            objective_target_deviation INTEGER NULL,
            objective_prefer_off INTEGER NULL,
            objective_prefer_work INTEGER NULL,
            generated_at TEXT NULL,
            confirmed_at TEXT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO schedule_months (year_month, status, objective_overstaff) "
        "VALUES ('2026-10', 'DRAFT', 5)"
    )
    initialize_database(conn)
    initialize_database(conn)

    columns = _columns(conn, "schedule_months")
    assert {"objective_max_deviation", "objective_max_overstaff"} <= set(columns)
    row = conn.execute("SELECT * FROM schedule_months").fetchone()
    assert (row["year_month"], row["objective_overstaff"]) == ("2026-10", 5)
    assert row["objective_max_deviation"] is None
    conn.close()


def test_migrate_is_idempotent_when_run_twice():
    """TEST08: 移行済みDBに対してinitialize_databaseを2回実行してもエラーにしない."""
    conn = get_connection(":memory:")
    _create_legacy_schema(conn)
    initialize_database(conn)
    initialize_database(conn)  # 2回目

    assert _columns(conn, "staff").count("skill_level") == 1
    rows = conn.execute("SELECT skill_level FROM staff ORDER BY staff_id").fetchall()
    assert [r[0] for r in rows] == [3, 3]
    conn.close()


def test_initialize_database_twice_on_fresh_db_is_idempotent():
    """TEST08: 新規DBでもinitialize_databaseを2回実行してエラーにしない."""
    conn = get_connection(":memory:")
    initialize_database(conn)
    initialize_database(conn)
    assert _columns(conn, "staff").count("skill_level") == 1
    conn.close()


@pytest.mark.parametrize("skill_level", [0, 6])
def test_check_skill_level_range_fresh_db(conn, skill_level):
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
                "max_consecutive_days, skill_level) VALUES ('a', 1, 480, 5, ?)",
                (skill_level,),
            )


@pytest.mark.parametrize("skill_level", [0, 6])
def test_check_skill_level_range_migrated_db(skill_level):
    conn = get_connection(":memory:")
    _create_legacy_schema(conn)
    initialize_database(conn)
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO staff (staff_name, role_id, daily_work_minutes, "
                "max_consecutive_days, skill_level) VALUES ('b', 1, 480, 5, ?)",
                (skill_level,),
            )
    conn.close()
