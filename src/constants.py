"""アプリ全体で使用する定数."""

# ロール: 1スタッフ = 1ロール（MVP1）
ROLE_LEADER = "LEADER"
ROLE_CHECKER = "CHECKER"
ROLE_CLEANER = "CLEANER"
ROLE_CODES = (ROLE_LEADER, ROLE_CHECKER, ROLE_CLEANER)

# スキルレベル: 清掃業務全般の総合スキルを1〜5の整数で保持する
SKILL_LEVEL_MIN = 1
SKILL_LEVEL_MAX = 5
SKILL_LEVEL_DEFAULT = 3
SKILL_LEVELS = {1: "初心者", 2: "初級", 3: "標準", 4: "上級", 5: "熟練"}

# 特殊スキル: 総合スキル(skill_level)とは別概念。「何ができるか」をマスターへ登録する。
# 列を増やし続けない（can_xxx をstaffへ追加しない）ため special_skills との多対多で持つ。
SPECIAL_SKILL_HEAVY_WORK = "HEAVY_WORK"
# MVPの初期登録分のみ。増やす場合はDBへ追加する（コードへ埋め込まない）
INITIAL_SPECIAL_SKILLS: tuple[tuple[str, str, int], ...] = (
    (SPECIAL_SKILL_HEAVY_WORK, "力仕事可", 10),
)

# 通常勤務曜日: 0=月 ... 6=日（month_utils.weekday_index と同じ基準）
WEEKDAY_MIN = 0
WEEKDAY_MAX = 6
WEEKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)

# 通常勤務時刻: HH:MM の文字列で保持する。翌日跨ぎ（例 30:00）は清掃の通常勤務では扱わない
STANDARD_TIME_HOUR_MAX = 23

# 目標勤務日数/週（Solverのsoft constraint用。未設定=制約なし）
TARGET_DAYS_PER_WEEK_MIN = 1
TARGET_DAYS_PER_WEEK_MAX = 7

# 勤怠CSVの勤務セル区分
# BLANK は「空欄」であり「休日」とは断定しない（元CSVの意味が変わっても対応できるように）
SHIFT_TYPE_TIME_RANGE = "TIME_RANGE"
SHIFT_TYPE_OTHER_DUTY = "OTHER_DUTY"
SHIFT_TYPE_BLANK = "BLANK"
SHIFT_TYPE_UNKNOWN = "UNKNOWN"
SHIFT_TYPES = (
    SHIFT_TYPE_TIME_RANGE,
    SHIFT_TYPE_OTHER_DUTY,
    SHIFT_TYPE_BLANK,
    SHIFT_TYPE_UNKNOWN,
)

# 勤怠インポートの状態: 各月ACTIVEは最大1件。再取込時は旧ACTIVEをSUPERSEDEDにする
IMPORT_STATUS_ACTIVE = "ACTIVE"
IMPORT_STATUS_SUPERSEDED = "SUPERSEDED"
IMPORT_STATUSES = (IMPORT_STATUS_ACTIVE, IMPORT_STATUS_SUPERSEDED)

# 勤怠CSV（freee人事労務の月間シフト出力）の固定列。以降は YYYY-MM-DD の日付列のみ
ATTENDANCE_COLUMN_EMPLOYEE_CODE = "従業員番号"
ATTENDANCE_COLUMN_EMPLOYEE_NAME = "freee人事労務での表示名"
ATTENDANCE_COLUMN_DEPARTMENT = "部門"
ATTENDANCE_FIXED_COLUMNS = (
    ATTENDANCE_COLUMN_EMPLOYEE_CODE,
    ATTENDANCE_COLUMN_EMPLOYEE_NAME,
    ATTENDANCE_COLUMN_DEPARTMENT,
)

# 勤怠CSVの文字コード（この順に試す）
CSV_ENCODINGS = ("utf-8-sig", "cp932")

# CSVの部門がこの値（前後空白除去後）かつ TIME_RANGE のセルだけを清掃勤務として数える
CLEANING_DEPARTMENT = "清掃"

# 時間帯ではない既知の業務（OTHER_DUTY）。清掃勤務には数えない。必要に応じて追加する
OTHER_DUTY_LABELS = ("深夜フロント（夜勤）",)

# TIME_RANGE（HH:MM-HH:MM）の許容範囲。24時超え（例 30:00 = 翌6:00）を許可する
SHIFT_HOUR_MAX = 47
SHIFT_MAX_DURATION_MINUTES = 24 * 60

# 日別検証の判定（重大度）. 優先順位 ERROR > WARNING > OK
VALIDATION_STATUS_OK = "OK"
VALIDATION_STATUS_WARNING = "WARNING"
VALIDATION_STATUS_ERROR = "ERROR"
VALIDATION_STATUSES = (VALIDATION_STATUS_OK, VALIDATION_STATUS_WARNING, VALIDATION_STATUS_ERROR)
