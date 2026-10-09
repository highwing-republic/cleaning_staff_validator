"""アプリ内で受け渡すデータ型."""

from dataclasses import dataclass, field

from src.constants import (
    GENERATION_STATUSES,
    SCHEDULE_DAY_DRAFT,
    SCHEDULE_DAY_FINALIZED,
    SCHEDULE_DAY_STATUSES,
    SCHEDULE_RUN_TYPES,
    SCHEDULE_SOURCE_GENERATED,
    SCHEDULE_SOURCES,
    IMPORT_STATUSES,
    SHIFT_TYPES,
    SKILL_LEVEL_DEFAULT,
    SOLVER_STATUS_OPTIMAL,
    SOLVER_STATUSES,
    TIME_STATUSES,
    VALIDATION_STATUSES,
    VALIDATION_STATUSES_CLEAR,
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
class StaffDatePreferenceInput:
    """ある1日の勤務希望（「今回だけ何が違うか」）. 1スタッフ1日につき1件.

    通常どおりの日はこの型のインスタンスを作らない（行がない = 通常条件を使う）。
    override_* は「その日に出勤するなら何時か」であり、出勤を強制しない。
    note は若女将の確認用で、Solverの条件には使わない（note だけの登録も可）。
    """

    staff_id: int
    work_date: str
    absolute_off: bool = False
    prefer_off: bool = False
    available_extra: bool = False
    override_start_time: str | None = None
    override_end_time: str | None = None
    note: str | None = None

    @property
    def is_empty(self) -> bool:
        """すべて通常どおりか（Trueなら保存せず行を削除する）."""
        return not (
            self.absolute_off
            or self.prefer_off
            or self.available_extra
            or self.override_start_time
            or self.override_end_time
            or (self.note or "").strip()
        )


@dataclass(frozen=True)
class StaffDayCondition:
    """通常条件と勤務希望を突き合わせた、ある1日の実効条件.

    将来Solverがこの結果を使う（DB・画面に依存せず resolve_staff_day_condition で作る）。
    - base_available: その日が通常勤務曜日か
    - can_work: 実際に勤務可能か（ABSOLUTE_OFF が最優先で False）
    - prefer_off: できれば休み（can_work は変えない）
    - effective_*: その日に勤務する場合の時刻。確定できない場合はNone
    - time_status: 実効時間の確定状況（TIME_STATUS_*）
    """

    staff_id: int
    work_date: str
    base_available: bool
    can_work: bool
    prefer_off: bool = False
    available_extra: bool = False
    absolute_off: bool = False
    effective_start_time: str | None = None
    effective_end_time: str | None = None
    time_status: str = TIME_STATUSES[0]
    # 通常勤務時刻ではなく勤務希望の時刻を採用したか（画面で変更点だけを示すために持つ）
    start_overridden: bool = False
    end_overridden: bool = False
    note: str | None = None
    has_preference: bool = False

    def __post_init__(self) -> None:
        if self.time_status not in TIME_STATUSES:
            raise ValueError(f"invalid time_status: {self.time_status!r}")


@dataclass(frozen=True)
class DailyRequirementInput:
    """日別の必要条件.

    required_total_staff は最低必要人数（完全一致人数ではない）。0も有効な設定で、
    「要件未設定」はこの行自体が存在しないことで表す。
    スキル条件は「skill_level >= required_skill_level の清掃勤務者が required_skill_count 名以上」。
    required_skill_count = 0 ならスキル条件なし。
    reserved_rooms は予約室数で、必要人数とは別の入力値（アプリは必要人数を推定しない）。
    None=未入力/未確認, 0=予約室数0 を区別する。
    """

    work_date: str
    required_total_staff: int
    max_total_staff: int | None = None
    occupancy_rate: float | None = None
    note: str | None = None
    required_skill_level: int | None = None
    required_skill_count: int = 0
    reserved_rooms: int | None = None


@dataclass(frozen=True)
class DailyRequirementView:
    """ある1日の要件（画面・サマリー用）.

    requirement=None は「要件未設定」（daily_requirements に行がない）を表す。
    required_total_staff=0 の「設定済み・必要人数0」とは別の状態。
    role_counts は role_id -> 必要人数（0は「そのRole条件なし」）。
    """

    work_date: str
    requirement: DailyRequirementInput | None = None
    role_counts: dict[int, int] = field(default_factory=dict)

    @property
    def is_defined(self) -> bool:
        return self.requirement is not None


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


# ---------------------------------------------------------------------------
# Shift generation（Phase 8）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationStaff:
    """シフト生成に渡すスタッフ1名. DB行ではなくSolver専用の入力型.

    day_conditions は work_date -> StaffDayCondition（通常条件と勤務希望を
    突き合わせた結果）。Solverは曜日・勤務希望を直接見ず、この結果だけを使う。
    max_consecutive_days が None なら連勤の上限なし。
    target_days_per_week が None なら、チェッカーは週5日、その他は
    最低必要人数の平均から自動目安を計算する。
    """

    staff_id: int
    employee_code: str
    staff_name: str
    role_id: int
    skill_level: int
    # Role固有の勤務方針に使う。DBのrole_idを決め打ちしないためcodeも渡す。
    role_code: str | None = None
    day_conditions: dict[str, StaffDayCondition] = field(default_factory=dict)
    max_consecutive_days: int | None = None
    # 週に何日程度勤務したいか（Noneなら目標なし）. Hard Constraintではない
    target_days_per_week: int | None = None


@dataclass(frozen=True)
class GenerationDay:
    """シフト生成に渡す1日分の需要.

    requirement_is_set=False は要件未設定（daily_requirements に行がない）。
    その日は人数・Role・Skillの制約を設定しない。
    reserved_rooms / occupancy_rate は参考値で、Phase 8 の制約には使わない。
    """

    work_date: str
    requirement_is_set: bool = False
    required_total_staff: int = 0
    max_total_staff: int | None = None
    role_requirements: dict[int, int] = field(default_factory=dict)
    required_skill_level: int | None = None
    required_skill_count: int = 0
    reserved_rooms: int | None = None


@dataclass(frozen=True)
class GenerationRequest:
    """シフト生成の入力一式（DB・Streamlitに依存しない）.

    prior_work_history は staff_id -> 期間開始直前の勤務日（'YYYY-MM-DD'）の集合。
    期間境界をまたぐ連勤を正しく数えるために使う。
    fixed_assignments は固定された勤務（手修正して固定した分と確定日の全スタッフ）。
    """

    work_dates: list[str]
    staff: list[GenerationStaff]
    days: list[GenerationDay]
    prior_work_history: dict[int, set[str]] = field(default_factory=dict)
    # (staff_id, work_date) -> 1=出勤で固定 / 0=休みで固定. Hard Constraint として扱い、
    # 公平性や希望休のために動かさない
    fixed_assignments: dict[tuple[int, str], int] = field(default_factory=dict)
    time_limit_seconds: float | None = None


@dataclass(frozen=True)
class GenerationIssue:
    """生成時に検出した問題1件. code は安定した識別子（例 STAFF_SHORTAGE）."""

    code: str
    message: str
    work_date: str | None = None
    staff_id: int | None = None
    staff_name: str | None = None
    role_id: int | None = None
    shortage: int | None = None


@dataclass(frozen=True)
class GeneratedAssignment:
    """生成された1スタッフ×1日の勤務. 休みの場合は時刻を持たない."""

    work_date: str
    staff_id: int
    staff_name: str
    is_working: bool
    start_time: str | None = None
    end_time: str | None = None


@dataclass(frozen=True)
class DailyGenerationResult:
    """1日分の生成結果と不足.

    role_shortages は role_id -> 不足人数（0の分は含めない）。
    status は GENERATION_STATUS_*（既存の検証statusとは別系統）。
    """

    work_date: str
    status: str
    requirement_is_set: bool
    scheduled_staff_count: int
    required_total_staff: int | None = None
    staff_shortage: int = 0
    role_shortages: dict[int, int] = field(default_factory=dict)
    skill_shortage: int = 0
    excluded_staff_count: int = 0

    def __post_init__(self) -> None:
        if self.status not in GENERATION_STATUSES:
            raise ValueError(f"invalid status: {self.status!r}")

    @property
    def total_shortage(self) -> int:
        return self.staff_shortage + sum(self.role_shortages.values()) + self.skill_shortage

    @property
    def has_shortage(self) -> bool:
        return self.total_shortage > 0


@dataclass(frozen=True)
class StaffGenerationSummary:
    """スタッフ1名分の勤務状況（希望休の尊重と目標勤務日数への近さ）.

    目標勤務日数の比較は整数スケールで行う（10日・11日などの期間で
    丸め方によって不自然にならないようにするため）。
        target_scaled = target_days_per_week * period_days
        actual_scaled = 7 * scheduled_days
        deviation_scaled = abs(actual_scaled - target_scaled)
    target_days_per_week が None でも、チェッカーは週5日、その他は期間の
    最低必要人数から求めた平均勤務量を自動目安として最適化する。
    この場合 target_scaled は値を持ち、target_days_per_week だけが None になる。
    画面では scaled 値をそのまま出さず、target_days（日数）へ戻して表示する。
    """

    staff_id: int
    staff_name: str
    scheduled_days: int
    period_days: int
    target_days_per_week: int | None = None
    target_scaled: int | None = None
    actual_scaled: int | None = None
    deviation_scaled: int | None = None
    prefer_off_requested_count: int = 0
    prefer_off_worked_count: int = 0

    @property
    def has_target(self) -> bool:
        return self.target_scaled is not None

    @property
    def target_is_automatic(self) -> bool:
        return self.target_days_per_week is None and self.target_scaled is not None

    @property
    def target_days(self) -> float | None:
        """期間に換算した目安日数（表示用. 例 14日・週3日 → 6.0）."""
        if self.target_scaled is None:
            return None
        return self.target_scaled / 7

    @property
    def prefer_off_respected_count(self) -> int:
        return self.prefer_off_requested_count - self.prefer_off_worked_count


@dataclass(frozen=True)
class ScheduleGenerationResult:
    """期間分の生成結果.

    solver_status は CP-SAT の結果をラップしたもの（SOLVER_STATUS_*）。
    不足を変数として許容するため、通常の入力では INFEASIBLE にはならない。
    assignments は出勤・休みの両方を含む（is_working で区別する）。
    """

    work_dates: list[str]
    solver_status: str = SOLVER_STATUS_OPTIMAL
    days: list[DailyGenerationResult] = field(default_factory=list)
    assignments: list[GeneratedAssignment] = field(default_factory=list)
    staff_summaries: list[StaffGenerationSummary] = field(default_factory=list)
    issues: list[GenerationIssue] = field(default_factory=list)
    solve_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.solver_status not in SOLVER_STATUSES:
            raise ValueError(f"invalid solver_status: {self.solver_status!r}")

    @property
    def has_solution(self) -> bool:
        return bool(self.days)

    @property
    def total_shortage(self) -> int:
        return sum(d.total_shortage for d in self.days)

    @property
    def total_workdays(self) -> int:
        return sum(1 for a in self.assignments if a.is_working)

    @property
    def shortage_days(self) -> list[DailyGenerationResult]:
        return [d for d in self.days if d.has_shortage]

    def working_assignments(self, staff_id: int) -> list[GeneratedAssignment]:
        return [a for a in self.assignments if a.staff_id == staff_id and a.is_working]

    def issues_with_code(self, code: str) -> list[GenerationIssue]:
        return [i for i in self.issues if i.code == code]

    def count_status(self, status: str) -> int:
        return sum(1 for d in self.days if d.status == status)

    # --- 希望休の尊重（最適化結果であり制約違反ではない） ---

    @property
    def prefer_off_requested_total(self) -> int:
        return sum(s.prefer_off_requested_count for s in self.staff_summaries)

    @property
    def prefer_off_worked_total(self) -> int:
        return sum(s.prefer_off_worked_count for s in self.staff_summaries)

    @property
    def prefer_off_respected_total(self) -> int:
        return self.prefer_off_requested_total - self.prefer_off_worked_total

    @property
    def total_target_deviation(self) -> int:
        """目標勤務日数からの乖離の合計（整数スケール）. 目標なしのスタッフは含まない."""
        return sum(
            s.deviation_scaled for s in self.staff_summaries if s.deviation_scaled is not None
        )

    def staff_summary(self, staff_id: int) -> StaffGenerationSummary | None:
        for summary in self.staff_summaries:
            if summary.staff_id == staff_id:
                return summary
        return None


# ---------------------------------------------------------------------------
# 現在の勤務表（Phase 10）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleRunRecord:
    """生成の実行履歴. 勤務表そのものの正本ではない."""

    run_id: int
    period_start: str
    period_end: str
    run_type: str
    solver_status: str
    total_shortage: int
    total_workdays: int
    created_at: str

    def __post_init__(self) -> None:
        if self.run_type not in SCHEDULE_RUN_TYPES:
            raise ValueError(f"invalid run_type: {self.run_type!r}")


@dataclass(frozen=True)
class ScheduleAssignmentRecord:
    """現在の勤務表の1件（work_date × staff_id につき1件）.

    休みの日も is_working=False で保持する（「この日は休みで固定」を表すため）。
    start_time / end_time はsnapshot。スタッフマスターを後から変えても
    すでに作成済みの勤務表の時刻は変わらない。
    source_run_id は GENERATED のとき生成したrun、MANUAL のときはNULL
    （手修正は特定のSolver結果に由来しないため）。
    """

    work_date: str
    staff_id: int
    is_working: bool
    start_time: str | None = None
    end_time: str | None = None
    locked: bool = False
    source: str = SCHEDULE_SOURCE_GENERATED
    source_run_id: int | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        if self.source not in SCHEDULE_SOURCES:
            raise ValueError(f"invalid source: {self.source!r}")
        if self.is_working and not (self.start_time and self.end_time):
            raise ValueError("working assignment requires start_time and end_time")
        if not self.is_working and (self.start_time or self.end_time):
            raise ValueError("day off must not have work times")


@dataclass(frozen=True)
class ScheduleDayRecord:
    """勤務表の1日分の状態."""

    work_date: str
    status: str = SCHEDULE_DAY_DRAFT
    latest_run_id: int | None = None
    updated_at: str | None = None
    finalized_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in SCHEDULE_DAY_STATUSES:
            raise ValueError(f"invalid status: {self.status!r}")

    @property
    def is_finalized(self) -> bool:
        return self.status == SCHEDULE_DAY_FINALIZED


@dataclass(frozen=True)
class ScheduleDayView:
    """画面向けの1日分. day=None は未作成（勤務表がまだない日）."""

    work_date: str
    day: ScheduleDayRecord | None = None
    assignments: dict[int, ScheduleAssignmentRecord] = field(default_factory=dict)

    @property
    def exists(self) -> bool:
        return self.day is not None

    @property
    def is_finalized(self) -> bool:
        return self.day is not None and self.day.is_finalized

    @property
    def is_editable(self) -> bool:
        """確定日は手修正・再生成の対象外."""
        return self.exists and not self.is_finalized

    @property
    def working_count(self) -> int:
        return sum(1 for a in self.assignments.values() if a.is_working)

    @property
    def locked_count(self) -> int:
        return sum(1 for a in self.assignments.values() if a.locked)


@dataclass(frozen=True)
class CurrentScheduleView:
    """対象期間の現在の勤務表."""

    work_dates: list[str]
    days: list[ScheduleDayView] = field(default_factory=list)

    @property
    def existing_dates(self) -> list[str]:
        return [d.work_date for d in self.days if d.exists]

    @property
    def missing_dates(self) -> list[str]:
        return [d.work_date for d in self.days if not d.exists]

    @property
    def draft_dates(self) -> list[str]:
        return [d.work_date for d in self.days if d.exists and not d.is_finalized]

    @property
    def finalized_dates(self) -> list[str]:
        return [d.work_date for d in self.days if d.is_finalized]

    def day(self, work_date: str) -> ScheduleDayView | None:
        for view in self.days:
            if view.work_date == work_date:
                return view
        return None

    def assignment(self, work_date: str, staff_id: int) -> ScheduleAssignmentRecord | None:
        view = self.day(work_date)
        return None if view is None else view.assignments.get(staff_id)


@dataclass(frozen=True)
class ScheduleChange:
    """再生成プレビューで変わる1件（現在 → 再生成後）."""

    work_date: str
    staff_id: int
    staff_name: str
    before_is_working: bool
    after_is_working: bool
    after_start_time: str | None = None
    after_end_time: str | None = None


@dataclass(frozen=True)
class FixedAssignmentConflict:
    """固定された勤務と現在の勤務可能条件の矛盾.

    固定を勝手に解除しないため、検出して利用者へ知らせるだけに留める。
    """

    work_date: str
    staff_id: int
    staff_name: str
    reason: str


@dataclass(frozen=True)
class RegenerationPreview:
    """再生成の下見. この時点ではDBを変更しない."""

    work_dates: list[str]
    result: ScheduleGenerationResult | None = None
    changes: list[ScheduleChange] = field(default_factory=list)
    conflicts: list[FixedAssignmentConflict] = field(default_factory=list)
    target_dates: list[str] = field(default_factory=list)
    skipped_finalized_dates: list[str] = field(default_factory=list)
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def can_apply(self) -> bool:
        return not self.errors and self.result is not None and self.result.has_solution

    @property
    def change_count(self) -> int:
        return len(self.changes)



# ---------------------------------------------------------------------------
# 現在勤務表の検証・出力（Phase 11）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleDayValidationResult:
    """保存済み勤務表1日分の検証結果.

    schedule_status が None の日は勤務表そのものが未作成で、人数・ロール・スキルは
    判定しない（staffing=None）。必要条件は表示のために保持する。
    staffing は既存の検証エンジン（staffing_validation）の判定結果をそのまま持つ。
    """

    work_date: str
    schedule_status: str | None = None
    staffing: DailyStaffingResult | None = None
    requirement: DailyRequirementInput | None = None
    scheduled_staff: int = 0
    missing_assignment_count: int = 0
    schedule_issues: list[ValidationIssue] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.schedule_status is not None and self.schedule_status not in SCHEDULE_DAY_STATUSES:
            raise ValueError(f"invalid schedule_status: {self.schedule_status!r}")

    @property
    def exists(self) -> bool:
        return self.schedule_status is not None

    @property
    def is_finalized(self) -> bool:
        return self.schedule_status == SCHEDULE_DAY_FINALIZED

    @property
    def is_draft(self) -> bool:
        return self.schedule_status == SCHEDULE_DAY_DRAFT

    @property
    def requirement_defined(self) -> bool:
        return self.requirement is not None

    @property
    def required_staff(self) -> int | None:
        return self.requirement.required_total_staff if self.requirement else None

    @property
    def reserved_rooms(self) -> int | None:
        return self.requirement.reserved_rooms if self.requirement else None

    @property
    def staff_shortage(self) -> int:
        """必要人数に対する不足（判定しない日は0）."""
        if self.staffing is None or self.staffing.required_staff is None:
            return 0
        return max(0, self.staffing.required_staff - self.staffing.actual_cleaning_staff)

    @property
    def issues(self) -> list[ValidationIssue]:
        """勤務表そのものの問題 → 体制の問題 の順に並べた全Issue."""
        staffing_issues = self.staffing.issues if self.staffing else []
        return [*self.schedule_issues, *staffing_issues]

    @property
    def status(self) -> str:
        """この日の総合判定（最も重大な severity）."""
        from src.staffing_validation import worst_status

        return worst_status(self.issues)

    @property
    def has_problem(self) -> bool:
        return self.status not in VALIDATION_STATUSES_CLEAR


@dataclass(frozen=True)
class SchedulePeriodValidationResult:
    """対象期間分の勤務表検証結果."""

    work_dates: list[str]
    days: list[ScheduleDayValidationResult] = field(default_factory=list)

    def day(self, work_date: str) -> ScheduleDayValidationResult | None:
        for day in self.days:
            if day.work_date == work_date:
                return day
        return None

    def count_status(self, status: str) -> int:
        return sum(1 for d in self.days if d.status == status)

    @property
    def existing_days(self) -> int:
        return sum(1 for d in self.days if d.exists)

    @property
    def missing_days(self) -> int:
        return sum(1 for d in self.days if not d.exists)

    @property
    def finalized_days(self) -> int:
        return sum(1 for d in self.days if d.is_finalized)

    @property
    def draft_days(self) -> int:
        return sum(1 for d in self.days if d.is_draft)

    @property
    def clear_days(self) -> int:
        """不足等の問題がない日数（下書きだけの日も含む）."""
        return sum(1 for d in self.days if not d.has_problem)

    @property
    def problem_days(self) -> int:
        return sum(1 for d in self.days if d.has_problem)

    @property
    def requirement_missing_days(self) -> int:
        return sum(1 for d in self.days if d.exists and not d.requirement_defined)

    @property
    def has_draft(self) -> bool:
        return any(d.is_draft for d in self.days)

    @property
    def issues(self) -> list[ValidationIssue]:
        return [issue for day in self.days for issue in day.issues]

    @property
    def total_shortage(self) -> int:
        return sum(d.staff_shortage for d in self.days)


@dataclass(frozen=True)
class ScheduleExportStaffRow:
    """勤務表Excelの1行（スタッフ1名）. cells は work_date -> 表示文字列."""

    staff: StaffInput
    role_name: str
    cells: dict[str, str] = field(default_factory=dict)

    @property
    def working_days(self) -> int:
        return sum(1 for value in self.cells.values() if ":" in value)


@dataclass(frozen=True)
class ScheduleExport:
    """勤務表Excelを組み立てるために必要な情報（DB非依存の受け渡し用）."""

    work_dates: list[str]
    validation: SchedulePeriodValidationResult
    staff_rows: list[ScheduleExportStaffRow] = field(default_factory=list)
    role_names: dict[int, str] = field(default_factory=dict)
    exported_at: str | None = None


@dataclass(frozen=True)
class HomeStatus:
    """ホーム画面に出す進行状況（対象期間のみを見る）."""

    work_dates: list[str]
    active_staff_count: int = 0
    staff_without_work_time: int = 0
    preference_days: int = 0
    requirement_days: int = 0
    schedule_days: int = 0
    finalized_days: int = 0

    @property
    def period_days(self) -> int:
        return len(self.work_dates)

    @property
    def has_staff(self) -> bool:
        return self.active_staff_count > 0
