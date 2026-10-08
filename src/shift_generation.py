"""OR-Tools CP-SAT による基本シフト生成（出勤/休みの決定）.

SQLite・Streamlitへは依存しない。Service層が集めた GenerationRequest だけを受け取る。

決定変数は x[staff, date] ∈ {0,1}（1=出勤）のみ。勤務時刻はSolverに決めさせず、
すでに確定している StaffDayCondition の effective_start_time / effective_end_time を使う
（通常09:00-15:30 + 早上がり13:00 なら 09:00-13:00 で出勤する）。

人数・Role・Skillの不足はHard Constraintにしない。不足変数を置いて最小化するため、
「必要8名・勤務可能6名」でも「6名配置・2名不足」として勤務案を返す
（現場では人員不足が構造的に起こるため、作成不能で終わらせない）。

最適化は2段階に分ける。
    Pass 1: 不足の合計を最小化
    Pass 2: Pass 1 の最適値に固定したうえで出勤日数を最小化
重み調整に依存せず、不足0のために全員出勤させる解を避けられる。
"""

import time

from ortools.sat.python import cp_model

from src.constants import (
    GENERATION_ISSUE_INVALID_WORK_TIME,
    GENERATION_ISSUE_REQUIREMENT_MISSING,
    GENERATION_ISSUE_ROLE_SHORTAGE,
    GENERATION_ISSUE_SKILL_SHORTAGE,
    GENERATION_ISSUE_STAFF_SHORTAGE,
    GENERATION_ISSUE_UNSET_WORK_TIME,
    GENERATION_STATUS_OK,
    GENERATION_STATUS_REQUIREMENT_MISSING,
    GENERATION_STATUS_SHORTAGE,
    SOLVE_TIME_LIMIT_SECONDS,
    SOLVER_STATUS_FEASIBLE,
    SOLVER_STATUS_INFEASIBLE,
    SOLVER_STATUS_MODEL_INVALID,
    SOLVER_STATUS_OPTIMAL,
    SOLVER_STATUS_UNKNOWN,
    TIME_STATUS_INVALID,
    TIME_STATUS_OK,
    TIME_STATUS_UNSET,
)
from src.models import (
    DailyGenerationResult,
    GeneratedAssignment,
    GenerationDay,
    GenerationIssue,
    GenerationRequest,
    GenerationStaff,
    ScheduleGenerationResult,
)
from src.period_utils import parse_date

_SOLVER_STATUS_BY_CODE = {
    cp_model.OPTIMAL: SOLVER_STATUS_OPTIMAL,
    cp_model.FEASIBLE: SOLVER_STATUS_FEASIBLE,
    cp_model.INFEASIBLE: SOLVER_STATUS_INFEASIBLE,
    cp_model.MODEL_INVALID: SOLVER_STATUS_MODEL_INVALID,
    cp_model.UNKNOWN: SOLVER_STATUS_UNKNOWN,
}


def generate_shift(request: GenerationRequest) -> ScheduleGenerationResult:
    """対象期間の出勤/休みを決定する.

    勤務可能な候補が1人もいない場合もエラーにせず、全員休みの案と不足を返す。
    """
    work_dates = list(request.work_dates)
    day_by_date = {day.work_date: day for day in request.days}
    days = [day_by_date.get(d) or GenerationDay(work_date=d) for d in work_dates]

    candidates, issues = _collect_candidates(request.staff, work_dates)
    issues += _requirement_missing_issues(days)

    if not work_dates:
        return ScheduleGenerationResult(work_dates=[], issues=issues)

    model = cp_model.CpModel()
    x = {
        (staff.staff_id, work_date): model.NewBoolVar(f"x_{staff.staff_id}_{work_date}")
        for staff in request.staff
        for work_date in work_dates
    }

    # 候補でない日は出勤させない（通常休み曜日・絶対休み・勤務時刻が確定しない日）
    for staff in request.staff:
        for work_date in work_dates:
            if work_date not in candidates.get(staff.staff_id, set()):
                model.Add(x[(staff.staff_id, work_date)] == 0)

    shortages = _add_requirement_constraints(model, x, request.staff, days)
    _add_max_consecutive_constraints(model, x, request, work_dates)

    total_shortage = sum(shortages.values()) if shortages else 0
    total_workdays = sum(x.values())

    time_limit = request.time_limit_seconds or SOLVE_TIME_LIMIT_SECONDS
    started = time.monotonic()

    # Pass 1: 不足の合計を最小化する
    model.Minimize(total_shortage)
    solver = _make_solver(time_limit)
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Pass 2: 不足をPass 1の値に固定し、その中で出勤日数を最小化する
        # （不足0のために全員出勤させる解を避けるため）
        best_shortage = int(round(solver.ObjectiveValue()))
        if shortages:
            model.Add(total_shortage == best_shortage)
        model.Minimize(total_workdays)
        solver = _make_solver(time_limit)
        status = solver.Solve(model)

    solve_seconds = time.monotonic() - started
    solver_status = _SOLVER_STATUS_BY_CODE.get(status, SOLVER_STATUS_UNKNOWN)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return ScheduleGenerationResult(
            work_dates=work_dates,
            solver_status=solver_status,
            issues=issues,
            solve_seconds=solve_seconds,
        )

    assignments = _build_assignments(solver, x, request.staff, work_dates, candidates)
    daily_results, shortage_issues = _build_daily_results(
        solver, x, request.staff, days, shortages, candidates
    )

    return ScheduleGenerationResult(
        work_dates=work_dates,
        solver_status=solver_status,
        days=daily_results,
        assignments=assignments,
        issues=issues + shortage_issues,
        solve_seconds=solve_seconds,
    )


def _make_solver(time_limit_seconds: float) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    # 同じ入力で同じ不足数・配置人数になるよう単一スレッドかつ固定シードで解く
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    return solver


# ---------------------------------------------------------------------------
# 候補スタッフの判定
# ---------------------------------------------------------------------------


def _collect_candidates(
    staff_list: list[GenerationStaff], work_dates: list[str]
) -> tuple[dict[int, set[str]], list[GenerationIssue]]:
    """各スタッフが出勤しうる日を集める.

    can_work かつ勤務時刻が確定している（time_status == OK）日だけを候補にする。
    勤務時刻が確定しない日は候補から除外し、勝手な時刻で勤務させない（issueとして残す）。
    """
    candidates: dict[int, set[str]] = {}
    issues: list[GenerationIssue] = []

    for staff in staff_list:
        available: set[str] = set()
        for work_date in work_dates:
            condition = staff.day_conditions.get(work_date)
            if condition is None or not condition.can_work:
                continue

            if condition.time_status == TIME_STATUS_OK:
                available.add(work_date)
                continue

            if condition.time_status == TIME_STATUS_UNSET:
                issues.append(
                    GenerationIssue(
                        code=GENERATION_ISSUE_UNSET_WORK_TIME,
                        message=(
                            f"{staff.staff_name}: 通常勤務時間が未設定のため"
                            "この日の候補から除外しました。"
                        ),
                        work_date=work_date,
                        staff_id=staff.staff_id,
                        staff_name=staff.staff_name,
                    )
                )
            elif condition.time_status == TIME_STATUS_INVALID:
                issues.append(
                    GenerationIssue(
                        code=GENERATION_ISSUE_INVALID_WORK_TIME,
                        message=(
                            f"{staff.staff_name}: 勤務時間が成立しないため"
                            "この日の候補から除外しました。"
                        ),
                        work_date=work_date,
                        staff_id=staff.staff_id,
                        staff_name=staff.staff_name,
                    )
                )
        candidates[staff.staff_id] = available

    return candidates, issues


def _requirement_missing_issues(days: list[GenerationDay]) -> list[GenerationIssue]:
    return [
        GenerationIssue(
            code=GENERATION_ISSUE_REQUIREMENT_MISSING,
            message="要件未設定のため人数・ロール・スキルの条件を設定していません。",
            work_date=day.work_date,
        )
        for day in days
        if not day.requirement_is_set
    ]


# ---------------------------------------------------------------------------
# 需要の制約（不足はSoft、最大人数はHard）
# ---------------------------------------------------------------------------


def _add_requirement_constraints(
    model: cp_model.CpModel,
    x: dict,
    staff_list: list[GenerationStaff],
    days: list[GenerationDay],
) -> dict[tuple, cp_model.IntVar]:
    """必要人数・Role・Skillの不足変数を作って制約を張る.

    戻り値は不足変数のdict（キーは ('staff', date) / ('role', date, role_id) /
    ('skill', date)）。要件未設定の日には何も設定しない。
    """
    shortages: dict[tuple, cp_model.IntVar] = {}

    for day in days:
        if not day.requirement_is_set:
            continue

        working_today = [x[(staff.staff_id, day.work_date)] for staff in staff_list]

        # 必要人数（不足を許容する）
        if day.required_total_staff > 0:
            shortage = model.NewIntVar(
                0, day.required_total_staff, f"staff_shortage_{day.work_date}"
            )
            model.Add(sum(working_today) + shortage >= day.required_total_staff)
            shortages[("staff", day.work_date)] = shortage

        # 最大人数はHard Constraint
        if day.max_total_staff is not None:
            model.Add(sum(working_today) <= day.max_total_staff)

        # ロール別必要人数（1スタッフ1ロール）
        for role_id, required_count in day.role_requirements.items():
            if required_count <= 0:
                continue
            role_working = [
                x[(staff.staff_id, day.work_date)]
                for staff in staff_list
                if staff.role_id == role_id
            ]
            shortage = model.NewIntVar(
                0, required_count, f"role_shortage_{day.work_date}_{role_id}"
            )
            model.Add(sum(role_working) + shortage >= required_count)
            shortages[("role", day.work_date, role_id)] = shortage

        # 総合スキル条件（skill_level >= required_skill_level が required_skill_count 名以上）
        if day.required_skill_count > 0 and day.required_skill_level is not None:
            skill_working = [
                x[(staff.staff_id, day.work_date)]
                for staff in staff_list
                if staff.skill_level >= day.required_skill_level
            ]
            shortage = model.NewIntVar(
                0, day.required_skill_count, f"skill_shortage_{day.work_date}"
            )
            model.Add(sum(skill_working) + shortage >= day.required_skill_count)
            shortages[("skill", day.work_date)] = shortage

    return shortages


# ---------------------------------------------------------------------------
# 最大連続勤務日数（期間境界を含む）
# ---------------------------------------------------------------------------


def _add_max_consecutive_constraints(
    model: cp_model.CpModel,
    x: dict,
    request: GenerationRequest,
    work_dates: list[str],
) -> None:
    """任意の (max+1) 連続日について出勤数が max 以下になるよう制約する.

    期間開始前の勤務実績（prior_work_history）も同じ窓に含めるため、
    「期間開始前に3連勤 + 今回3連勤」のような超過も検出できる。
    """
    for staff in request.staff:
        limit = staff.max_consecutive_days
        if limit is None or limit <= 0:
            continue

        window = limit + 1
        prior = request.prior_work_history.get(staff.staff_id, set())
        # 期間開始前の日付を、期間の日付列の前に連結して同じ窓で数える
        timeline = _prior_dates(work_dates[0], window - 1) + work_dates

        for start in range(len(timeline) - window + 1):
            terms = []
            constant = 0
            for work_date in timeline[start : start + window]:
                key = (staff.staff_id, work_date)
                if key in x:
                    terms.append(x[key])
                elif work_date in prior:
                    constant += 1
            if not terms:
                continue
            model.Add(sum(terms) <= limit - constant)


def _prior_dates(first_work_date: str, count: int) -> list[str]:
    """期間初日の直前 count 日分の日付を昇順で返す."""
    from datetime import timedelta

    if count <= 0:
        return []
    first = parse_date(first_work_date)
    if first is None:
        raise ValueError(f"work_date must be YYYY-MM-DD: {first_work_date!r}")
    return [(first - timedelta(days=offset)).isoformat() for offset in range(count, 0, -1)]


# ---------------------------------------------------------------------------
# 結果の組み立て
# ---------------------------------------------------------------------------


def _build_assignments(
    solver: cp_model.CpSolver,
    x: dict,
    staff_list: list[GenerationStaff],
    work_dates: list[str],
    candidates: dict[int, set[str]],
) -> list[GeneratedAssignment]:
    """出勤・休みの両方を返す. 勤務時刻は StaffDayCondition の実効時間を使う."""
    assignments: list[GeneratedAssignment] = []
    for work_date in work_dates:
        for staff in staff_list:
            is_working = bool(solver.Value(x[(staff.staff_id, work_date)]))
            condition = staff.day_conditions.get(work_date)
            assignments.append(
                GeneratedAssignment(
                    work_date=work_date,
                    staff_id=staff.staff_id,
                    staff_name=staff.staff_name,
                    is_working=is_working,
                    start_time=condition.effective_start_time if is_working and condition else None,
                    end_time=condition.effective_end_time if is_working and condition else None,
                )
            )
    return assignments


def _build_daily_results(
    solver: cp_model.CpSolver,
    x: dict,
    staff_list: list[GenerationStaff],
    days: list[GenerationDay],
    shortages: dict[tuple, cp_model.IntVar],
    candidates: dict[int, set[str]],
) -> tuple[list[DailyGenerationResult], list[GenerationIssue]]:
    results: list[DailyGenerationResult] = []
    issues: list[GenerationIssue] = []

    for day in days:
        work_date = day.work_date
        scheduled = sum(
            int(bool(solver.Value(x[(staff.staff_id, work_date)]))) for staff in staff_list
        )
        excluded = sum(
            1 for staff in staff_list if work_date not in candidates.get(staff.staff_id, set())
        )

        staff_shortage = _shortage_value(solver, shortages.get(("staff", work_date)))
        skill_shortage = _shortage_value(solver, shortages.get(("skill", work_date)))
        role_shortages = {}
        for role_id in day.role_requirements:
            value = _shortage_value(solver, shortages.get(("role", work_date, role_id)))
            if value > 0:
                role_shortages[role_id] = value

        if not day.requirement_is_set:
            status = GENERATION_STATUS_REQUIREMENT_MISSING
        elif staff_shortage or skill_shortage or role_shortages:
            status = GENERATION_STATUS_SHORTAGE
        else:
            status = GENERATION_STATUS_OK

        results.append(
            DailyGenerationResult(
                work_date=work_date,
                status=status,
                requirement_is_set=day.requirement_is_set,
                scheduled_staff_count=scheduled,
                required_total_staff=day.required_total_staff if day.requirement_is_set else None,
                staff_shortage=staff_shortage,
                role_shortages=role_shortages,
                skill_shortage=skill_shortage,
                excluded_staff_count=excluded,
            )
        )

        if staff_shortage:
            issues.append(
                GenerationIssue(
                    code=GENERATION_ISSUE_STAFF_SHORTAGE,
                    message=f"必要人数に対して{staff_shortage}名不足しています。",
                    work_date=work_date,
                    shortage=staff_shortage,
                )
            )
        for role_id, value in role_shortages.items():
            issues.append(
                GenerationIssue(
                    code=GENERATION_ISSUE_ROLE_SHORTAGE,
                    message=f"ロールの必要人数に対して{value}名不足しています。",
                    work_date=work_date,
                    role_id=role_id,
                    shortage=value,
                )
            )
        if skill_shortage:
            issues.append(
                GenerationIssue(
                    code=GENERATION_ISSUE_SKILL_SHORTAGE,
                    message=f"スキル条件に対して{skill_shortage}名不足しています。",
                    work_date=work_date,
                    shortage=skill_shortage,
                )
            )

    return results, issues


def _shortage_value(solver: cp_model.CpSolver, variable) -> int:
    return 0 if variable is None else int(solver.Value(variable))
