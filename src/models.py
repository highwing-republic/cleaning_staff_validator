"""アプリ内で受け渡すデータ型（§41）.

T05で固定。以降、破壊的変更禁止（フィールド追加はデフォルト値付きのみ）。
"""

from dataclasses import dataclass, field

from src.constants import (
    PREFERENCE_TYPES,
    SKILL_LEVEL_DEFAULT,
    SOLVER_STATUS_FEASIBLE,
    SOLVER_STATUS_OPTIMAL,
    SOLVER_STATUSES,
    STAGES,
)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StaffInput:
    """スタッフマスター + 通常勤務可能曜日."""

    staff_id: int
    staff_name: str
    role_id: int
    daily_work_minutes: int
    max_consecutive_days: int
    active: bool = True
    # weekday(0=Monday..6=Sunday) -> is_available。7曜日揃っていない場合はPC04で検出する
    weekday_availability: dict[int, bool] = field(default_factory=dict)
    # 清掃業務の総合スキル(1〜5)。表示・保存のみに使用し、シフト最適化には使用しない（§25-§28）
    skill_level: int = SKILL_LEVEL_DEFAULT

    def is_available_on(self, weekday: int) -> bool:
        """通常勤務可能曜日か. データ欠落時はFalse."""
        return self.weekday_availability.get(weekday, False)


@dataclass(frozen=True)
class MonthlyConditionInput:
    staff_id: int
    year_month: str
    target_monthly_minutes: int
    min_monthly_minutes: int | None = None
    max_monthly_minutes: int | None = None
    carryover_consecutive_days: int = 0


@dataclass(frozen=True)
class DailyRequirementInput:
    work_date: str
    required_total_staff: int
    max_total_staff: int | None = None
    occupancy_rate: float | None = None
    note: str | None = None


@dataclass(frozen=True)
class RoleRequirementInput:
    work_date: str
    role_id: int
    required_count: int


@dataclass(frozen=True)
class PreferenceInput:
    staff_id: int
    work_date: str
    preference_type: str

    def __post_init__(self) -> None:
        if self.preference_type not in PREFERENCE_TYPES:
            raise ValueError(f"invalid preference_type: {self.preference_type!r}")


@dataclass(frozen=True)
class LockedAssignmentInput:
    """固定セル. is_working=True なら固定出勤(HC11)、False なら固定休日(HC12)."""

    staff_id: int
    work_date: str
    is_working: bool


@dataclass(frozen=True)
class SchedulerInput:
    """Solverへの入力一式. staffにはinactiveを含んでよい（Solver側で除外する）."""

    year_month: str
    staff: list[StaffInput] = field(default_factory=list)
    monthly_conditions: list[MonthlyConditionInput] = field(default_factory=list)
    daily_requirements: list[DailyRequirementInput] = field(default_factory=list)
    role_requirements: list[RoleRequirementInput] = field(default_factory=list)
    preferences: list[PreferenceInput] = field(default_factory=list)
    locked_assignments: list[LockedAssignmentInput] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssignmentResult:
    staff_id: int
    work_date: str
    is_working: bool


@dataclass(frozen=True)
class StageObjectiveResult:
    """段階最適化の1Stage分の結果. 実行したStageのみ作る."""

    stage: int  # 1..len(STAGES)
    solver_status: str
    objective_value: int | None = None

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(f"invalid stage: {self.stage!r}")
        if self.solver_status not in SOLVER_STATUSES:
            raise ValueError(f"invalid solver_status: {self.solver_status!r}")


@dataclass(frozen=True)
class SchedulerResult:
    """Solver全体の結果（§37, §38.4, §41）.

    INFEASIBLE / UNKNOWN 時は assignments = []。未実行Stageのobjective値は None。
    objective_overstaff は最適化対象ではなく、解の「最低人数を超える出勤」合計（v1.4）。
    """

    status: str
    assignments: list[AssignmentResult] = field(default_factory=list)
    completed_stage: int = 0

    objective_overstaff: int | None = None
    objective_target_deviation: int | None = None
    objective_prefer_off: int | None = None
    objective_prefer_work: int | None = None

    stage_results: list[StageObjectiveResult] = field(default_factory=list)

    # v1.4 追加（Stage 2: 差の最大値 / Stage 3: 日別超過人数の最大値）
    objective_max_deviation: int | None = None
    objective_max_overstaff: int | None = None

    def __post_init__(self) -> None:
        if self.status not in SOLVER_STATUSES:
            raise ValueError(f"invalid status: {self.status!r}")
        if not 0 <= self.completed_stage <= len(STAGES):
            raise ValueError(f"invalid completed_stage: {self.completed_stage!r}")

    @property
    def has_solution(self) -> bool:
        """保存可能なシフトがあるか（OPTIMAL / FEASIBLE, T74）."""
        return self.status in (SOLVER_STATUS_OPTIMAL, SOLVER_STATUS_FEASIBLE)


# ---------------------------------------------------------------------------
# Validation / Precheck
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationError:
    """入力検証・事前チェック・シフト検証で共通の違反1件.

    例外ではなく値として返す。code は "PC05" 等のチェックID、または検証項目名。
    """

    code: str
    message: str
    staff_id: int | None = None
    work_date: str | None = None
    role_id: int | None = None
    field_name: str | None = None


@dataclass(frozen=True)
class PrecheckResult:
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        return not self.errors
