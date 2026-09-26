"""SQLite接続とスキーマ初期化."""

import sqlite3
from pathlib import Path

from src.constants import (
    IMPORT_STATUS_ACTIVE,
    IMPORT_STATUSES,
    SHIFT_TYPE_TIME_RANGE,
    SHIFT_TYPES,
    SKILL_LEVEL_DEFAULT,
    SKILL_LEVEL_MAX,
    SKILL_LEVEL_MIN,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


class IncompatibleSchemaError(RuntimeError):
    """元アプリ（月間シフト自動作成）のDBに接続した場合."""


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
    _ensure_compatible_schema(conn)

    shift_types = _sql_list(SHIFT_TYPES)
    import_statuses = _sql_list(IMPORT_STATUSES)

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

        # employee_code は勤怠CSVの従業員番号。先頭0等を保つため必ずTEXTで保持する
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS staff (
                staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_code TEXT UNIQUE NOT NULL CHECK (length(trim(employee_code)) > 0),
                staff_name TEXT NOT NULL,
                department TEXT NULL,
                role_id INTEGER NOT NULL,
                skill_level INTEGER NOT NULL DEFAULT {SKILL_LEVEL_DEFAULT}
                    CHECK (skill_level BETWEEN {SKILL_LEVEL_MIN} AND {SKILL_LEVEL_MAX}),
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                FOREIGN KEY (role_id) REFERENCES roles (role_id)
            )
            """
        )

        # required_total_staff は最低必要人数。スキル条件は
        # 「skill_level >= required_skill_level の清掃勤務者が required_skill_count 名以上」
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS daily_requirements (
                work_date TEXT PRIMARY KEY,
                occupancy_rate REAL NULL,
                required_total_staff INTEGER NOT NULL,
                max_total_staff INTEGER NULL,
                note TEXT NULL,
                required_skill_level INTEGER NULL
                    CHECK (required_skill_level IS NULL
                           OR required_skill_level BETWEEN {SKILL_LEVEL_MIN} AND {SKILL_LEVEL_MAX}),
                required_skill_count INTEGER NOT NULL DEFAULT 0
                    CHECK (required_skill_count >= 0),
                CHECK (required_skill_count = 0 OR required_skill_level IS NOT NULL)
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
            CREATE TABLE IF NOT EXISTS attendance_imports (
                import_id INTEGER PRIMARY KEY AUTOINCREMENT,
                year_month TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                employee_count INTEGER NOT NULL CHECK (employee_count >= 0),
                shift_count INTEGER NOT NULL CHECK (shift_count >= 0),
                unmatched_count INTEGER NOT NULL CHECK (unmatched_count >= 0),
                status TEXT NOT NULL CHECK (status IN ({import_statuses}))
            )
            """
        )
        # 各月ACTIVEは最大1件
        conn.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_attendance_imports_active_month
            ON attendance_imports (year_month)
            WHERE status = '{IMPORT_STATUS_ACTIVE}'
            """
        )

        # staff_id は未登録スタッフの場合NULL。raw_shift は元CSV値をそのまま保持する
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS attendance_shifts (
                shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
                import_id INTEGER NOT NULL,
                staff_id INTEGER NULL,
                employee_code TEXT NOT NULL,
                employee_name TEXT NULL,
                department TEXT NULL,
                work_date TEXT NOT NULL,
                raw_shift TEXT NOT NULL DEFAULT '',
                shift_type TEXT NOT NULL CHECK (shift_type IN ({shift_types})),
                start_minutes INTEGER NULL,
                end_minutes INTEGER NULL,
                available_for_cleaning INTEGER NOT NULL CHECK (available_for_cleaning IN (0, 1)),
                UNIQUE (import_id, employee_code, work_date),
                CHECK (
                    (shift_type = '{SHIFT_TYPE_TIME_RANGE}')
                    = (start_minutes IS NOT NULL AND end_minutes IS NOT NULL)
                ),
                CHECK (available_for_cleaning = 0 OR shift_type = '{SHIFT_TYPE_TIME_RANGE}'),
                FOREIGN KEY (import_id) REFERENCES attendance_imports (import_id),
                FOREIGN KEY (staff_id) REFERENCES staff (staff_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_attendance_shifts_import_date
            ON attendance_shifts (import_id, work_date)
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


def _ensure_compatible_schema(conn: sqlite3.Connection) -> None:
    """元アプリのDB（employee_codeのないstaffテーブル）への接続を拒否する.

    CREATE TABLE IF NOT EXISTS は既存の旧テーブルをそのまま残すため、
    誤って元アプリのDBを指定した場合に黙って動作しないよう明示的に止める。
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(staff)")}
    if columns and "employee_code" not in columns:
        raise IncompatibleSchemaError(
            "このDBは清掃人員検証アプリのスキーマではありません"
            "（staff.employee_code がありません）。DBパスを確認してください。"
        )
