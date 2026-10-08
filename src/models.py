"""アプリ内で受け渡すデータ型."""

from dataclasses import dataclass, field

from src.constants import (
    IMPORT_STATUSES,
    SHIFT_TYPES,
    SKILL_LEVEL_DEFAULT,
    VALIDATION_STATUSES,
)


# ---------------------------------------------------------------------------
# Master / Requirement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StaffInput:
    """スタッフマスター. 勤怠CSVとの照合キーは employee_code（文字列）.

    standard_* 以降は「普段どう働く人か」（通常勤務条件）。その日だけの早上がり・遅出は
    ここには持たず、期間ごとの勤務希望として扱う。
    通常勤務曜日と特殊スキルは別テーブルなのでこの型には持たせない（StaffDetail を使う）。
    standard_work_minutes も保持しない（work_time.standard_work_minutes で計算する）。
    未設定はNone。不明な条件を既定値で埋めない（誤った条件でシフトを組まないため）。
    """

    staff_id: int
    employee_code: str
    staff_name: str
    role_id: int
    # 清掃業務の総合スキル(1〜5)
    skill_level: int = SKILL_LEVEL_DEFAULT
    department: str | None = None
    active: bool = True
    # 通常勤務時刻 'HH:MM'（00:00〜23:59, 翌日跨ぎなし）
    standard_start_time: str | None = None
    standard_end_time: str | None = None
    # 週に何日程度勤務したいか（将来Solverのsoft constraintで使う。None=目標なし）
    target_days_per_week: int | None = None
    # 期間内の最大勤務日数（将来用の欄。Phase 5では判定に使わない）
    max_days_per_period: int | None = None
    max_consecutive_days: int | None = None


@dataclass(frozen=True)
class SpecialSkill:
    """特殊スキルのマスター（例 HEAVY_WORK / 力仕事可）.

    総合スキル(skill_level)とは別概念で、できる作業の種類を表す。
    """

    special_skill_id: int
    skill_code: str
    skill_name: str
    active: bool = True
    display_order: int = 0


@dataclass(frozen=True)
class StaffDetail:
    """スタッフ1名の通常勤務条件をまとめた型（画面・保存で使う）.

    weekdays は通常勤務する曜日（0=月〜6=日）の昇順。未設定なら空。
    special_skill_ids は付与済み特殊スキルのID昇順。
    """

    staff: StaffInput
    weekdays: tuple[int, ...] = ()
    special_skill_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class DailyRequirementInput:
    """日別の必要条件.

    required_total_staff は最低必要人数（完全一致人数ではない）。
    スキル条件は「skill_level >= required_skill_level の清掃勤務者が required_skill_count 名以上」。
    required_skill_count = 0 ならスキル条件なし。
    """

    work_date: str
    required_total_staff: int
    max_total_staff: int | None = None
    occupancy_rate: float | None = None
    note: str | None = None
    required_skill_level: int | None = None
    required_skill_count: int = 0


@dataclass(frozen=True)
class RoleRequirementInput:
    work_date: str
    role_id: int
    required_count: int


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttendanceShiftInput:
    """勤怠CSVの1セル（1従業員 × 1日）.

    staff_id は staff マスターと照合できない場合 None（未登録スタッフ）。
    raw_shift は元CSVの値をそのまま保持する（UNKNOWNでも失わない）。
    start_minutes / end_minutes は0時起点の分。24時超え（例 30:00 = 1800）も有効。
    """

    employee_code: str
    work_date: str
    raw_shift: str
    shift_type: str
    available_for_cleaning: bool
    staff_id: int | None = None
    employee_name: str | None = None
    department: str | None = None
    start_minutes: int | None = None
    end_minutes: int | None = None

    def __post_init__(self) -> None:
        if self.shift_type not in SHIFT_TYPES:
            raise ValueError(f"invalid shift_type: {self.shift_type!r}")


@dataclass(frozen=True)
class AttendanceImportRecord:
    import_id: int
    year_month: str
    source_filename: str
    imported_at: str
    employee_count: int
    shift_count: int
    unmatched_count: int
    status: str

    def __post_init__(self) -> None:
        if self.status not in IMPORT_STATUSES:
            raise ValueError(f"invalid status: {self.status!r}")


@dataclass(frozen=True)
class ImportIssue:
    """勤怠CSV取込の問題1件（fatal error / warning 共通）.

    row_number はCSVの行番号（ヘッダー=1行目）。該当しない場合None。
    """

    code: str
    message: str
    employee_code: str | None = None
    employee_name: str | None = None
    work_date: str | None = None
    row_number: int | None = None


@dataclass(frozen=True)
class AttendanceParseResult:
    """勤怠CSVの解析結果（DB非依存, staff未照合なので shifts の staff_id は全てNone）.

    errors が1件でもあれば取込不可。year_month は日付列から判定できない場合None。
    """

    source_filename: str
    year_month: str | None = None
    shifts: list[AttendanceShiftInput] = field(default_factory=list)
    errors: list[ImportIssue] = field(default_factory=list)
    warnings: list[ImportIssue] = field(default_factory=list)


@dataclass(frozen=True)
class AttendancePreview:
    """staff master照合済みの取込前プレビュー. 件数は全て保存時の定義と同じ.

    - employee_count: CSV内の従業員番号の種類数
    - shift_count: BLANK以外のセル数（画面表示名「入力済みシフトセル数」）
    - unmatched_count: staff masterと照合できなかった従業員番号の種類数
    - cleaning_employee_count: CSVの部門が清掃の従業員数
    - *_mismatch_count / inactive_staff_count: 該当する従業員数
    """

    source_filename: str
    year_month: str | None = None
    shifts: list[AttendanceShiftInput] = field(default_factory=list)
    errors: list[ImportIssue] = field(default_factory=list)
    warnings: list[ImportIssue] = field(default_factory=list)
    employee_count: int = 0
    shift_count: int = 0
    unmatched_count: int = 0
    unknown_shift_count: int = 0
    cleaning_employee_count: int = 0
    name_mismatch_count: int = 0
    department_mismatch_count: int = 0
    inactive_staff_count: int = 0

    @property
    def can_import(self) -> bool:
        return not self.errors and self.year_month is not None and bool(self.shifts)


# ---------------------------------------------------------------------------
# Daily staffing validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationIssue:
    """日別検証の問題1件. code は安定した識別子（例 STAFF_SHORTAGE）.

    required / actual / possible は判定に使った人数（該当しない場合None）。
    possible は UNKNOWN勤務・未登録スタッフが全員条件を満たすと仮定した最大人数。
    """

    code: str
    severity: str
    message: str
    work_date: str
    required: int | None = None
    actual: int | None = None
    possible: int | None = None
    role_id: int | None = None

    def __post_init__(self) -> None:
        if self.severity not in VALIDATION_STATUSES:
            raise ValueError(f"invalid severity: {self.severity!r}")


@dataclass(frozen=True)
class DailyStaffingResult:
    """1日分の清掃体制の集計と判定.

    requirement_defined=False（daily_requirements 行なし）の日は required_* が None で、
    人数・上限・ロール・スキルは判定しない（0人必要とはみなさない）。
    actual_roles / actual_skill_count は確定値（staff master照合済みの清掃勤務者のみ）、
    possible_* はUNKNOWN勤務・未登録スタッフを含めた最大可能人数。
    """

    work_date: str
    status: str
    requirement_defined: bool
    actual_cleaning_staff: int
    unmatched_working_count: int
    unknown_cleaning_shift_count: int
    required_staff: int | None = None
    max_total_staff: int | None = None
    required_roles: dict[int, int] = field(default_factory=dict)
    actual_roles: dict[int, int] = field(default_factory=dict)
    possible_roles: dict[int, int] = field(default_factory=dict)
    required_skill_level: int | None = None
    required_skill_count: int = 0
    actual_skill_count: int | None = None
    possible_skill_count: int | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in VALIDATION_STATUSES:
            raise ValueError(f"invalid status: {self.status!r}")


@dataclass(frozen=True)
class MonthlyValidationResult:
    """月間検証結果. 対象月にACTIVE取込がない場合 import_record=None, days=[]."""

    year_month: str
    import_record: AttendanceImportRecord | None = None
    days: list[DailyStaffingResult] = field(default_factory=list)

    @property
    def has_import(self) -> bool:
        return self.import_record is not None

    def count_status(self, status: str) -> int:
        return sum(1 for d in self.days if d.status == status)

    @property
    def requirement_missing_days(self) -> int:
        return sum(1 for d in self.days if not d.requirement_defined)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationError:
    """入力検証・日別検証で共通の違反1件.

    例外ではなく値として返す。code は検証項目名。
    """

    code: str
    message: str
    staff_id: int | None = None
    work_date: str | None = None
    role_id: int | None = None
    field_name: str | None = None
