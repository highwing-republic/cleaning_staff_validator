"""SQLite接続とスキーマ初期化（§17-§27）."""

import sqlite3
from pathlib import Path

from src.constants import (
    PREFERENCE_TYPES,
    SCHEDULE_STATUS,
    SKILL_LEVEL_DEFAULT,
    SKILL_LEVEL_MAX,
    SKILL_LEVEL_MIN,
    SOURCE_TYPES,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


def _sql_list(values: tuple[str, ...]) -> str:
    """CHECK制約用にPython定数からSQLのIN句リテラルを作る."""
    return ", ".join(f"'{v}'" for v in values)


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """DB接続を作成する. db_path未指定時はDEFAULT_DB_PATHを使用する."""
    if db_path is None:
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        target: str | Path = DEFAULT_DB_PATH
    elif db_path == ":memory:":
        target = db_path
    else:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        target = path

    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database(conn: sqlite3.Connection) -> None:
    """全テーブルを作成し、rolesの初期値を投入する（冪等）."""
    preference_types = _sql_list(PREFERENCE_TYPES)
    schedule_status = _sql_list(SCHEDULE_STATUS)
    source_types = _sql_list(SOURCE_TYPES)

    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS roles (
                role_id INTEGER PRIMARY KEY,
                role_code TEXT UNIQUE NOT NULL,
                role_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
            )
            """
        )

        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS staff (
                staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_name TEXT NOT NULL,
                role_id INTEGER NOT NULL,
                daily_work_minutes INTEGER NOT NULL,
                max_consecutive_days INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                skill_level INTEGER NOT NULL DEFAULT {SKILL_LEVEL_DEFAULT}
                    CHECK (skill_level BETWEEN {SKILL_LEVEL_MIN} AND {SKILL_LEVEL_MAX}),
                FOREIGN KEY (role_id) REFERENCES roles (role_id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_weekday_availability (
                staff_id INTEGER NOT NULL,
                weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
                is_available INTEGER NOT NULL CHECK (is_available IN (0, 1)),
                PRIMARY KEY (staff_id, weekday),
                FOREIGN KEY (staff_id) REFERENCES staff (staff_id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_monthly_conditions (
                staff_id INTEGER NOT NULL,
                year_month TEXT NOT NULL,
                target_monthly_minutes INTEGER NOT NULL,
                min_monthly_minutes INTEGER NULL,
                max_monthly_minutes INTEGER NULL,
                carryover_consecutive_days INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (staff_id, year_month),
                FOREIGN KEY (staff_id) REFERENCES staff (staff_id)
            )
            """
        )

        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS staff_day_preferences (
                staff_id INTEGER NOT NULL,
                work_date TEXT NOT NULL,
                preference_type TEXT NOT NULL CHECK (preference_type IN ({preference_types})),
                PRIMARY KEY (staff_id, work_date),
                FOREIGN KEY (staff_id) REFERENCES staff (staff_id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_requirements (
                work_date TEXT PRIMARY KEY,
                occupancy_rate REAL NULL,
                required_total_staff INTEGER NOT NULL,
                max_total_staff INTEGER NULL,
                note TEXT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_role_requirements (
                work_date TEXT NOT NULL,
                role_id INTEGER NOT NULL,
                required_count INTEGER NOT NULL,
                PRIMARY KEY (work_date, role_id),
                FOREIGN KEY (role_id) REFERENCES roles (role_id)
            )
            """
        )

        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS schedule_months (
                year_month TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ({schedule_status})),
                solver_status TEXT NULL,
                objective_overstaff INTEGER NULL,
                objective_target_deviation INTEGER NULL,
                objective_prefer_off INTEGER NULL,
                objective_prefer_work INTEGER NULL,
                objective_max_deviation INTEGER NULL,
                objective_max_overstaff INTEGER NULL,
                generated_at TEXT NULL,
                confirmed_at TEXT NULL
            )
            """
        )

        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS schedule_assignments (
                staff_id INTEGER NOT NULL,
                work_date TEXT NOT NULL,
                is_working INTEGER NOT NULL CHECK (is_working IN (0, 1)),
                is_locked INTEGER NOT NULL DEFAULT 0 CHECK (is_locked IN (0, 1)),
                source TEXT NOT NULL CHECK (source IN ({source_types})),
                PRIMARY KEY (staff_id, work_date),
                FOREIGN KEY (staff_id) REFERENCES staff (staff_id)
            )
            """
        )

        conn.executemany(
            "INSERT OR IGNORE INTO roles (role_id, role_code, role_name) VALUES (?, ?, ?)",
            [
                (1, "LEADER", "リーダー"),
                (2, "CHECKER", "チェッカー"),
                (3, "CLEANER", "クリーナー"),
            ],
        )

        _migrate_staff_skill_level(conn)
        _migrate_schedule_month_objectives(conn)


def _migrate_staff_skill_level(conn: sqlite3.Connection) -> None:
    """既存DBにskill_level列がなければ追加する（冪等, §7-§11）."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(staff)")}
    if "skill_level" in columns:
        return
    conn.execute(
        f"ALTER TABLE staff ADD COLUMN skill_level INTEGER NOT NULL "
        f"DEFAULT {SKILL_LEVEL_DEFAULT} "
        f"CHECK (skill_level BETWEEN {SKILL_LEVEL_MIN} AND {SKILL_LEVEL_MAX})"
    )


def _migrate_schedule_month_objectives(conn: sqlite3.Connection) -> None:
    """既存DBに v1.4 の目的値列がなければ追加する（冪等）."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(schedule_months)")}
    for column in ("objective_max_deviation", "objective_max_overstaff"):
        if column not in columns:
            conn.execute(f"ALTER TABLE schedule_months ADD COLUMN {column} INTEGER NULL")
