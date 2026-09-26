"""アプリ内で受け渡すデータ型."""

from dataclasses import dataclass

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
