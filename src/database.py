"""SQLite接続とスキーマ初期化."""

import sqlite3
from pathlib import Path

from src.constants import (
    IMPORT_STATUS_ACTIVE,
    IMPORT_STATUSES,
    INITIAL_SPECIAL_SKILLS,
    SHIFT_TYPE_TIME_RANGE,
    SHIFT_TYPES,
    SKILL_LEVEL_DEFAULT,
    SKILL_LEVEL_MAX,
    SKILL_LEVEL_MIN,
    STANDARD_TIME_HOUR_MAX,
    TARGET_DAYS_PER_WEEK_MAX,
    TARGET_DAYS_PER_WEEK_MIN,
    WEEKDAY_MAX,
    WEEKDAY_MIN,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

# Phase 5 で staff に追加した列（通常勤務条件）.
# 既存DBへは ALTER TABLE で後から追加するため、CREATE TABLE の末尾と同じ順・同じ定義にして
# 新規作成DBと移行後DBの列構成が一致するようにしている。
def _hhmm_check(column: str) -> str:
    """'HH:MM'（00:00〜23:59, 0埋め）だけを受け付けるCHECK式.

    GLOB だけでは 24:00〜29:59 を通してしまうため時の値も見る。
    0埋めを強制することで、終了>開始 の比較を文字列比較で正しく行える。
    """
    return (
        f"{column} GLOB '[0-2][0-9]:[0-5][0-9]' "
        f"AND CAST(substr({column}, 1, 2) AS INTEGER) <= {STANDARD_TIME_HOUR_MAX}"
    )


STAFF_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "standard_start_time",
        "standard_start_time TEXT NULL "
        f"CHECK (standard_start_time IS NULL OR ({_hhmm_check('standard_start_time')}))",
    ),
    (
        # 終了>開始 を列レベルCHECKに入れておくことで、ALTER TABLE で移行した既存DBでも
        # 新規作成DBと同じ制約が効く（テーブルレベルCHECKはALTERで追加できない）
        "standard_end_time",
        "standard_end_time TEXT NULL "
        f"CHECK (standard_end_time IS NULL OR ({_hhmm_check('standard_end_time')} "
        "AND (standard_start_time IS NULL OR standard_end_time > standard_start_time)))",
    ),
    (
        "target_days_per_week",
        "target_days_per_week INTEGER NULL CHECK (target_days_per_week IS NULL OR "
        f"target_days_per_week BETWEEN {TARGET_DAYS_PER_WEEK_MIN} AND {TARGET_DAYS_PER_WEEK_MAX})",
    ),
    (
        "max_days_per_period",
        "max_days_per_period INTEGER NULL "
        "CHECK (max_days_per_period IS NULL OR max_days_per_period > 0)",
    ),
    (
        "max_consecutive_days",
        "max_consecutive_days INTEGER NULL "
        "CHECK (max_consecutive_days IS NULL OR max_consecutive_days > 0)",
    ),
)


# Phase 7 で daily_requirements に追加した列（予約室数）.
# staff と同様、既存DBへは ALTER TABLE で追加するため CREATE TABLE の末尾と同じ順・
# 同じ定義にして新規作成DBと移行後DBの列構成・制約を一致させる。
# NULL（未入力・未確認）と 0（予約室数0）を区別する。
DAILY_REQUIREMENT_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "reserved_rooms",
        "reserved_rooms INTEGER NULL CHECK (reserved_rooms IS NULL OR reserved_rooms >= 0)",
    ),
)


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
    """全テーブルを作成し、roles・special_skillsの初期値を投入する（冪等）."""
    _ensure_compatible_schema(conn)

    shift_types = _sql_list(SHIFT_TYPES)
    import_statuses = _sql_list(IMPORT_STATUSES)
    staff_added_columns = ",\n                ".join(ddl for _, ddl in STAFF_ADDED_COLUMNS)
    requirement_added_columns = ",\n                ".join(
        ddl for _, ddl in DAILY_REQUIREMENT_ADDED_COLUMNS
    )

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

        # employee_code は勤怠CSVの従業員番号。先頭0等を保つため必ずTEXTで保持する。
        # standard_* 以降は通常勤務条件（Phase 5）。その日だけの変更は保持しない（勤務希望で扱う）。
        # standard_work_minutes は保存せず work_time.standard_work_minutes で計算する。
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
                {staff_added_columns},
                FOREIGN KEY (role_id) REFERENCES roles (role_id)
            )
            """
        )
        _migrate_staff_columns(conn)

        # 総合スキル(staff.skill_level)とは別概念。「力仕事可」等をstaffの列にせず多対多で持つ
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS special_skills (
                special_skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_code TEXT UNIQUE NOT NULL CHECK (length(trim(skill_code)) > 0),
                skill_name TEXT UNIQUE NOT NULL CHECK (length(trim(skill_name)) > 0),
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                display_order INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_special_skills (
                staff_id INTEGER NOT NULL,
                special_skill_id INTEGER NOT NULL,
                PRIMARY KEY (staff_id, special_skill_id),
                FOREIGN KEY (staff_id) REFERENCES staff (staff_id),
                FOREIGN KEY (special_skill_id) REFERENCES special_skills (special_skill_id)
            )
            """
        )

        # 通常勤務曜日。曜日をstaffの文字列1列に持たせない（Solverで扱えるよう行で持つ）。
        # Phase 5では曜日別の勤務時刻は持たず、勤務時刻は staff.standard_* を使う
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS staff_weekday_patterns (
                staff_id INTEGER NOT NULL,
                weekday INTEGER NOT NULL
                    CHECK (weekday BETWEEN {WEEKDAY_MIN} AND {WEEKDAY_MAX}),
                is_available INTEGER NOT NULL DEFAULT 1 CHECK (is_available IN (0, 1)),
                PRIMARY KEY (staff_id, weekday),
                FOREIGN KEY (staff_id) REFERENCES staff (staff_id)
            )
            """
        )

        # 期間別勤務希望（「今回だけ何が違うか」）。通常どおりの日は行を作らない。
        # 期間そのものはテーブルにせず日付で持つ（期間が重なっても希望を動かさずに済む）。
        # override_* は「その日に出勤するなら何時か」で、出勤を強制するものではない。
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS staff_date_preferences (
                preference_id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                work_date TEXT NOT NULL
                    -- 暦上ありえない日付を弾く。date('2026-02-30') は月末を検証せず
                    -- 入力をそのまま返すため、ユリウス日へ往復させて一致を見る
                    -- （'2026-02-30' -> '2026-03-02' で不一致, '2026-13-01' -> NULL）。
                    -- SQLiteのCHECKはNULLを成立と扱うので = ではなく IS で比較する
                    CHECK (work_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                           AND date(julianday(work_date)) IS work_date),
                absolute_off INTEGER NOT NULL DEFAULT 0 CHECK (absolute_off IN (0, 1)),
                prefer_off INTEGER NOT NULL DEFAULT 0 CHECK (prefer_off IN (0, 1)),
                available_extra INTEGER NOT NULL DEFAULT 0 CHECK (available_extra IN (0, 1)),
                override_start_time TEXT NULL
                    CHECK (override_start_time IS NULL
                           OR ({_hhmm_check('override_start_time')})),
                override_end_time TEXT NULL
                    CHECK (override_end_time IS NULL
                           OR ({_hhmm_check('override_end_time')}
                               AND (override_start_time IS NULL
                                    OR override_end_time > override_start_time))),
                note TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                -- 絶対休みの日に勤務時刻や通常外勤務可を併せ持っても意味がない
                CHECK (absolute_off = 0
                       OR (available_extra = 0
                           AND override_start_time IS NULL
                           AND override_end_time IS NULL)),
                UNIQUE (staff_id, work_date),
                FOREIGN KEY (staff_id) REFERENCES staff (staff_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_staff_date_preferences_date
            ON staff_date_preferences (work_date)
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
                {requirement_added_columns},
                CHECK (required_skill_count = 0 OR required_skill_level IS NOT NULL)
            )
            """
        )
        _migrate_daily_requirement_columns(conn)

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

        # skill_code の重複を無視するため何度実行しても増えない。
        # 既存行の名称・有効/無効は上書きしない（運用側で変更した内容を壊さないため）
        conn.executemany(
            "INSERT OR IGNORE INTO special_skills (skill_code, skill_name, display_order) "
            "VALUES (?, ?, ?)",
            list(INITIAL_SPECIAL_SKILLS),
        )


def _migrate_staff_columns(conn: sqlite3.Connection) -> None:
    """既存DBのstaffへPhase 5の列を追加する（冪等, 既存データは保持する）.

    CREATE TABLE IF NOT EXISTS は既存テーブルを変更しないため、
    Phase 1〜4のDBをそのまま使い続けられるようALTER TABLEで列だけ足す。
    既定値は入れない（勤務曜日・勤務時刻を推測して埋めると誤った条件で
    シフトが組まれるため、不明な情報はNULLのまま残す）。
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(staff)")}
    if not existing:
        return
    for column, ddl in STAFF_ADDED_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE staff ADD COLUMN {ddl}")


def _migrate_daily_requirement_columns(conn: sqlite3.Connection) -> None:
    """既存DBの daily_requirements へPhase 7の列を追加する（冪等, 既存データは保持する）.

    既定値は入れない（予約室数を0で埋めると「予約0室」と「未入力」の区別がつかなくなる）。
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(daily_requirements)")}
    if not existing:
        return
    for column, ddl in DAILY_REQUIREMENT_ADDED_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE daily_requirements ADD COLUMN {ddl}")


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
