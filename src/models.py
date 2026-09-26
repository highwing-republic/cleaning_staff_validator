"""アプリ内で受け渡すデータ型."""

from dataclasses import dataclass, field

from src.constants import IMPORT_STATUSES, SHIFT_TYPES, SKILL_LEVEL_DEFAULT


# ---------------------------------------------------------------------------
# Master / Requirement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StaffInput:
    """スタッフマスター. 勤怠CSVとの照合キーは employee_code（文字列）."""

    staff_id: int
    employee_code: str
    staff_name: str
    role_id: int
    # 清掃業務の総合スキル(1〜5)
    skill_level: int = SKILL_LEVEL_DEFAULT
    department: str | None = None
    active: bool = True


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
