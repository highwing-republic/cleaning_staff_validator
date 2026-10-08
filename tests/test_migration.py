"""Phase 1〜4のDBからPhase 5スキーマへの移行テスト.

Phase 1〜4のDB（staffに通常勤務条件の列がない状態）を作り、
Phase 5のコードで開いても既存データが失われないことと、
新規作成DBと同じ列構成・同じ制約になることを確認する。
"""

import sqlite3

import pytest

from src import repositories as repo
from src.database import (
    DAILY_REQUIREMENT_ADDED_COLUMNS,
    STAFF_ADDED_COLUMNS,
    get_connection,
    initialize_database,
)
from src.models import DailyRequirementInput

# Phase 1〜4 時点の staff / daily_requirements（通常勤務条件の列がない）
LEGACY_SCHEMA = """
CREATE TABLE roles (
    role_id INTEGER PRIMARY KEY,
    role_code TEXT UNIQUE NOT NULL,
    role_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);
CREATE TABLE staff (
    staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_code TEXT UNIQUE NOT NULL CHECK (length(trim(employee_code)) > 0),
    staff_name TEXT NOT NULL,
    department TEXT NULL,
    role_id INTEGER NOT NULL,
    skill_level INTEGER NOT NULL DEFAULT 3 CHECK (skill_level BETWEEN 1 AND 5),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    FOREIGN KEY (role_id) REFERENCES roles (role_id)
);
CREATE TABLE daily_requirements (
    work_date TEXT PRIMARY KEY,
    occupancy_rate REAL NULL,
    required_total_staff INTEGER NOT NULL,
    max_total_staff INTEGER NULL,
    note TEXT NULL,
    required_skill_level INTEGER NULL,
    required_skill_count INTEGER NOT NULL DEFAULT 0
);
INSERT INTO roles (role_id, role_code, role_name) VALUES
    (1, 'LEADER', 'リーダー'), (2, 'CHECKER', 'チェッカー'), (3, 'CLEANER', 'クリーナー');
INSERT INTO staff (employee_code, staff_name, department, role_id, skill_level) VALUES
    ('0007', '既存Aさん', '清掃', 3, 4),
    ('0008', '既存Bさん', '清掃', 1, 5);
INSERT INTO daily_requirements (work_date, required_total_staff) VALUES ('2026-10-01', 5);
"""


def _columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


@pytest.fixture()
def legacy_db(tmp_path):
    """Phase 1〜4相当のDBファイルを作って返す."""
    path = tmp_path / "phase4.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(LEGACY_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def migrated(legacy_db):
    conn = get_connection(str(legacy_db))
    initialize_database(conn)
    yield conn
    conn.close()


def test_existing_staff_is_preserved(migrated):
    rows = migrated.execute(
        "SELECT employee_code, staff_name, department, role_id, skill_level, active "
        "FROM staff ORDER BY staff_id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("0007", "既存Aさん", "清掃", 3, 4, 1),
        ("0008", "既存Bさん", "清掃", 1, 5, 1),
    ]


def test_existing_requirements_are_preserved(migrated):
    rows = migrated.execute(
        "SELECT work_date, required_total_staff FROM daily_requirements"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("2026-10-01", 5)]


def test_new_columns_are_null_not_guessed(migrated):
    """曜日・勤務時刻を推測して埋めないこと（誤った条件でシフトを組まないため）."""
    rows = migrated.execute(
        "SELECT standard_start_time, standard_end_time, target_days_per_week, "
        "max_days_per_period, max_consecutive_days FROM staff"
    ).fetchall()
    assert all(tuple(r) == (None, None, None, None, None) for r in rows)


def test_no_weekday_or_special_skill_rows_are_created(migrated):
    assert migrated.execute("SELECT COUNT(*) FROM staff_weekday_patterns").fetchone()[0] == 0
    assert migrated.execute("SELECT COUNT(*) FROM staff_special_skills").fetchone()[0] == 0


def test_new_tables_are_created(migrated):
    tables = {
        row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"special_skills", "staff_special_skills", "staff_weekday_patterns"} <= tables


def test_special_skill_is_seeded_on_migration(migrated):
    rows = migrated.execute("SELECT skill_code, skill_name FROM special_skills").fetchall()
    assert [tuple(r) for r in rows] == [("HEAVY_WORK", "力仕事可")]


def test_migrated_columns_match_fresh_database(migrated):
    fresh = get_connection(":memory:")
    initialize_database(fresh)
    assert _columns(migrated, "staff") == _columns(fresh, "staff")
    fresh.close()


def test_added_columns_are_all_present(migrated):
    columns = set(_columns(migrated, "staff"))
    assert {name for name, _ in STAFF_ADDED_COLUMNS} <= columns


def test_migration_is_idempotent(migrated):
    before = _columns(migrated, "staff")
    for _ in range(3):
        initialize_database(migrated)
    assert _columns(migrated, "staff") == before
    assert migrated.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 2
    assert migrated.execute("SELECT COUNT(*) FROM special_skills").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("start", "end"),
    [("9:00", None), ("24:00", None), ("09:00", "09:00"), ("15:00", "09:00")],
)
def test_migrated_db_enforces_same_time_constraints(migrated, start, end):
    """ALTER TABLE で移行したDBでも新規作成DBと同じ時刻制約が効くこと."""
    with pytest.raises(sqlite3.IntegrityError):
        with migrated:
            migrated.execute(
                "UPDATE staff SET standard_start_time = ?, standard_end_time = ? "
                "WHERE employee_code = '0007'",
                (start, end),
            )


def test_migrated_db_accepts_phase5_updates(migrated):
    """移行後のDBで通常勤務条件・曜日・特殊スキルを保存できること."""
    staff = repo.get_staff_by_employee_code(migrated, "0007")
    skill_id = repo.list_special_skills(migrated)[0].special_skill_id
    repo.update_staff(
        migrated,
        staff.staff_id,
        employee_code="0007",
        staff_name="既存Aさん",
        role_id=3,
        skill_level=4,
        department="清掃",
        standard_start_time="09:00",
        standard_end_time="15:30",
        target_days_per_week=4,
        max_consecutive_days=5,
        weekdays=[0, 1, 3, 4, 5],
        special_skill_ids=[skill_id],
    )
    detail = repo.get_staff_detail(migrated, staff.staff_id)
    assert detail.staff.standard_start_time == "09:00"
    assert detail.staff.standard_end_time == "15:30"
    assert detail.staff.target_days_per_week == 4
    assert detail.staff.max_consecutive_days == 5
    assert detail.weekdays == (0, 1, 3, 4, 5)
    assert detail.special_skill_ids == (skill_id,)


# ---------------------------------------------------------------------------
# daily_requirements（Phase 7: reserved_rooms）
# ---------------------------------------------------------------------------


def test_reserved_rooms_column_is_added(migrated):
    columns = _columns(migrated, "daily_requirements")
    assert {name for name, _ in DAILY_REQUIREMENT_ADDED_COLUMNS} <= set(columns)


def test_existing_requirements_keep_their_values(migrated):
    row = migrated.execute(
        "SELECT work_date, required_total_staff FROM daily_requirements"
    ).fetchone()
    assert tuple(row) == ("2026-10-01", 5)


def test_reserved_rooms_is_null_for_existing_rows(migrated):
    """予約室数を0で埋めない（「予約0室」と「未入力」の区別がつかなくなるため）."""
    rooms = migrated.execute("SELECT reserved_rooms FROM daily_requirements").fetchone()[0]
    assert rooms is None


def test_migrated_requirement_columns_match_fresh_database(migrated):
    fresh = get_connection(":memory:")
    initialize_database(fresh)
    assert _columns(migrated, "daily_requirements") == _columns(fresh, "daily_requirements")
    fresh.close()


def test_requirement_migration_is_idempotent(migrated):
    before = _columns(migrated, "daily_requirements")
    for _ in range(3):
        initialize_database(migrated)
    assert _columns(migrated, "daily_requirements") == before
    assert migrated.execute("SELECT COUNT(*) FROM daily_requirements").fetchone()[0] == 1


@pytest.mark.parametrize("value", [None, 0, 10])
def test_migrated_db_accepts_valid_reserved_rooms(migrated, value):
    with migrated:
        migrated.execute("UPDATE daily_requirements SET reserved_rooms = ?", (value,))
    assert migrated.execute("SELECT reserved_rooms FROM daily_requirements").fetchone()[0] == value


@pytest.mark.parametrize("value", [-1, -10])
def test_migrated_db_enforces_reserved_rooms_constraint(migrated, value):
    """ALTER TABLE で移行したDBでも新規作成DBと同じ制約が効くこと."""
    with pytest.raises(sqlite3.IntegrityError):
        with migrated:
            migrated.execute("UPDATE daily_requirements SET reserved_rooms = ?", (value,))


def test_migrated_db_accepts_phase7_requirement_save(migrated):
    repo.save_daily_requirement(
        migrated,
        DailyRequirementInput(
            work_date="2026-10-20", required_total_staff=4, reserved_rooms=8
        ),
    )
    saved = repo.get_daily_requirement(migrated, "2026-10-20")
    assert (saved.required_total_staff, saved.reserved_rooms) == (4, 8)

