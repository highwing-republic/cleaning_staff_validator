"""画面から呼ばれる業務処理（T73, T74, T79〜T84）.

UIはこのモジュール経由でDB・事前チェック・Solver・シフト検証を組み合わせる。
CONFIRMED月の書き込み禁止（§48）はrepository層でも拒否されるが、ここでも先に確認する。
"""

import sqlite3
from dataclasses import dataclass, field

from src import repositories as repo
from src.models import (
    AssignmentResult,
    PrecheckResult,
    SchedulerInput,
    SchedulerResult,
    ValidationError,
)
from src.precheck import run_precheck
from src.schedule_validation import validate_schedule
from src.scheduler import generate_schedule

ScheduleConfirmedError = repo.ScheduleConfirmedError


@dataclass(frozen=True)
class GenerationOutcome:
    """シフト生成（再計算）の結果.

    precheck が NG なら Solver は実行せず result は None。
    saved は OPTIMAL / FEASIBLE で保存した場合のみ True（T74）。
    """

    precheck: PrecheckResult
    result: SchedulerResult | None = None
    saved: bool = False


@dataclass(frozen=True)
class ConfirmOutcome:
    confirmed: bool
    errors: list[ValidationError] = field(default_factory=list)


def get_role_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["role_id"]: r["role_name"] for r in repo.list_roles(conn)}


def load_scheduler_input(conn: sqlite3.Connection, year_month: str) -> SchedulerInput:
    """対象月のSolver入力をDBから組み立てる. 固定セルは schedule_assignments の LOCK から取る."""
    return SchedulerInput(
        year_month=year_month,
        staff=repo.list_staff(conn, include_inactive=True),
        monthly_conditions=repo.get_monthly_conditions(conn, year_month),
        daily_requirements=repo.get_daily_requirements(conn, year_month),
        role_requirements=repo.get_role_requirements(conn, year_month),
        preferences=repo.get_preferences(conn, year_month),
        locked_assignments=repo.get_locked_assignments(conn, year_month),
    )


def run_precheck_for_month(conn: sqlite3.Connection, year_month: str) -> PrecheckResult:
    return run_precheck(load_scheduler_input(conn, year_month), get_role_names(conn))


def generate_and_save(conn: sqlite3.Connection, year_month: str) -> GenerationOutcome:
    """事前チェック → Solver → 保存（初回生成・固定を残した再計算の両方, T73/T74/T82）.

    - LOCKセルは Solver の Hard Constraint として維持され、保存時も行が保持される。
    - 未LOCKのMANUAL変更・OPTIMIZEDセルは上書きされる（§47）。
    - INFEASIBLE / UNKNOWN の場合は既存データを変更しない。
    """
    _ensure_editable(conn, year_month)
    scheduler_input = load_scheduler_input(conn, year_month)
    precheck = run_precheck(scheduler_input, get_role_names(conn))
    if not precheck.is_ok:
        return GenerationOutcome(precheck=precheck)

    result = generate_schedule(scheduler_input)
    if not result.has_solution:
        return GenerationOutcome(precheck=precheck, result=result)

    repo.save_generated_schedule(conn, year_month, result)
    return GenerationOutcome(precheck=precheck, result=result, saved=True)


def load_assignment_results(conn: sqlite3.Connection, year_month: str) -> list[AssignmentResult]:
    return [
        AssignmentResult(a.staff_id, a.work_date, a.is_working)
        for a in repo.load_assignments(conn, year_month)
    ]


def validate_current_schedule(conn: sqlite3.Connection, year_month: str) -> list[ValidationError]:
    """保存済みシフトを全Hard Constraintで検証する（§45）."""
    return validate_schedule(
        load_scheduler_input(conn, year_month),
        load_assignment_results(conn, year_month),
        role_names=get_role_names(conn),
    )


def apply_manual_edit(
    conn: sqlite3.Connection,
    staff_id: int,
    work_date: str,
    is_working: bool,
    is_locked: bool | None = None,
) -> list[ValidationError]:
    """手動変更（T79〜T81）. 変更後のシフト検証結果を返す.

    Hard違反があっても変更自体は保存する（確定時に拒否する, §45）。
    is_locked=None ならロック状態は変更しない（手動変更だけでは固定されない, §46）。
    """
    year_month = work_date[:7]
    _ensure_editable(conn, year_month)
    if repo.get_schedule_month(conn, year_month) is None:
        raise ValueError("シフトが未作成です。先にシフトを生成してください。")

    repo.update_assignment_manual(conn, staff_id, work_date, is_working)
    if is_locked is not None:
        repo.set_lock(conn, staff_id, work_date, is_locked)
    return validate_current_schedule(conn, year_month)


def set_lock(conn: sqlite3.Connection, staff_id: int, work_date: str, is_locked: bool) -> None:
    """固定 / 固定解除のみ行う（T81）."""
    _ensure_editable(conn, work_date[:7])
    repo.set_lock(conn, staff_id, work_date, is_locked)


def confirm_month(conn: sqlite3.Connection, year_month: str) -> ConfirmOutcome:
    """全検証が成功した場合のみ確定する（T83）."""
    _ensure_editable(conn, year_month)
    if repo.get_schedule_month(conn, year_month) is None:
        raise ValueError("シフトが未作成です。先にシフトを生成してください。")

    errors = validate_current_schedule(conn, year_month)
    if errors:
        return ConfirmOutcome(confirmed=False, errors=errors)
    repo.confirm_schedule_month(conn, year_month)
    return ConfirmOutcome(confirmed=True)


def unconfirm_month(conn: sqlite3.Connection, year_month: str) -> None:
    """確定を解除して再編集できる状態（DRAFT）に戻す（v1.5 §48）.

    勤務データ・固定セルはそのまま残る。確定時にダウンロードしたExcelは別途保管すること（§49）。
    """
    if not repo.is_month_confirmed(conn, year_month):
        raise ValueError(f"{year_month} は確定されていません。")
    repo.unconfirm_schedule_month(conn, year_month)


def is_confirmed(conn: sqlite3.Connection, year_month: str) -> bool:
    return repo.is_month_confirmed(conn, year_month)


def _ensure_editable(conn: sqlite3.Connection, year_month: str) -> None:
    """T84: CONFIRMED月の edit / generate / regenerate / lock変更 を拒否する."""
    if repo.is_month_confirmed(conn, year_month):
        raise ScheduleConfirmedError(f"{year_month} は確定済みのため変更できません。")
