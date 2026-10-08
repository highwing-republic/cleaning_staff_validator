"""OR-Tools CP-SAT による基本シフト生成（出勤/休みの決定）.

SQLite・Streamlitへは依存しない。Service層が集めた GenerationRequest だけを受け取る。

決定変数は x[staff, date] ∈ {0,1}（1=出勤）のみ。勤務時刻はSolverに決めさせず、
すでに確定している StaffDayCondition の effective_start_time / effective_end_time を使う
（通常09:00-15:30 + 早上がり13:00 なら 09:00-13:00 で出勤する）。

人数・Role・Skillの不足はHard Constraintにしない。不足変数を置いて最小化するため、
「必要8名・勤務可能6名」でも「6名配置・2名不足」として勤務案を返す
（現場では人員不足が構造的に起こるため、作成不能で終わらせない）。

最適化は辞書式に4段階へ分ける（重み付き単一目的にはしない）。
    Pass 1: 不足の合計を最小化
    Pass 2: Pass 1 の最適値に固定したうえで総出勤日数を最小化
    Pass 3: Pass 1・2 を固定したうえで PREFER_OFF 違反数を最小化
    Pass 4: Pass 1〜3 を固定したうえで目標勤務日数からの乖離を最小化
不足0のために全員出勤させる解を避けつつ、同じ不足・同じ総出勤日数の中で
「誰を出勤させるか」を希望休と目標勤務日数で決める。重み調整に依存しない。

総出勤日数の最小化を希望休より先に置くのは、このアプリの目的が
「必要な清掃体制を過剰配置せずに作る」ことだからである。目標勤務日数へ
近づけるために必要以上に人を出勤させてはならない。
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
    StaffGenerationSummary,
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

    objectives = _objective_factories(model, x, request.staff, work_dates, shortages)

    total_budget = request.time_limit_seconds or SOLVE_TIME_LIMIT_SECONDS
    started = time.monotonic()
    values, solver_status = _solve_lexicographic(model, x, objectives, started, total_budget)
    solve_seconds = time.monotonic() - started

    if values is None:
        return ScheduleGenerationResult(
            work_dates=work_dates,
            solver_status=solver_status,
            issues=issues,
            solve_seconds=solve_seconds,
        )

    assignments = _build_assignments(values, request.staff, work_dates)
    daily_results, shortage_issues = _build_daily_results(
        values, request.staff, days, candidates
    )
    staff_summaries = _build_staff_summaries(values, request.staff, work_dates)

    return ScheduleGenerationResult(
        work_dates=work_dates,
        solver_status=solver_status,
        days=daily_results,
        assignments=assignments,
        staff_summaries=staff_summaries,
        issues=issues + shortage_issues,
        solve_seconds=solve_seconds,
    )


# ---------------------------------------------------------------------------
# 辞書式の多段階最適化
# ---------------------------------------------------------------------------


def _objective_factories(
    model: cp_model.CpModel,
    x: dict,
    staff_list: list[GenerationStaff],
    work_dates: list[str],
    shortages: dict,
) -> list[tuple[str, object]]:
    """優先順位の高い順に (名前, 目的式を作る関数) を並べて返す.

    各段階は前段階の最適値を等式で固定したうえで最小化する（重みでまとめない）。
    定数になる段階（該当データがない場合）は省く。

    目的式を関数にしているのは、その段階で初めて必要になる補助変数・制約を
    そのときだけモデルへ追加するため。目標勤務日数の絶対値制約を最初から
    入れると、前段階（総出勤日数の最小化）の最適性証明が極端に遅くなる。
    """
    factories: list[tuple[str, object]] = []

    if shortages:
        factories.append(("shortage", lambda: sum(shortages.values())))

    if x:
        factories.append(("workdays", lambda: sum(x.values())))

    prefer_off_terms = [
        x[(staff.staff_id, work_date)]
        for staff in staff_list
        for work_date in work_dates
        if _wants_off(staff, work_date)
    ]
    if prefer_off_terms:
        factories.append(("prefer_off", lambda: sum(prefer_off_terms)))

    if any(staff.target_days_per_week is not None for staff in staff_list):
        factories.append(
            (
                "target_deviation",
                lambda: sum(_add_target_deviations(model, x, staff_list, work_dates)),
            )
        )

    return factories


def _wants_off(staff: GenerationStaff, work_date: str) -> bool:
    condition = staff.day_conditions.get(work_date)
    return bool(condition and condition.prefer_off)


def _add_target_deviations(
    model: cp_model.CpModel,
    x: dict,
    staff_list: list[GenerationStaff],
    work_dates: list[str],
) -> list:
    """目標勤務日数からの乖離を整数スケールで作る.

    小数を避けるため 7 倍したスケールで比較する。
        actual_scaled = 7 * 出勤日数
        target_scaled = target_days_per_week * 期間日数
    丸めを挟まないので、10日・11日などの期間でも不自然にならない。
    """
    period_days = len(work_dates)
    deviations = []

    for staff in staff_list:
        target = staff.target_days_per_week
        if target is None:
            continue
        target_scaled = target * period_days
        # 乖離は「全日休み」と「全日出勤」の2端のうち遠い方が最大
        upper = max(target_scaled, 7 * period_days - target_scaled)
        deviation = model.NewIntVar(0, upper, f"target_dev_{staff.staff_id}")
        worked = sum(x[(staff.staff_id, work_date)] for work_date in work_dates)
        model.AddAbsEquality(deviation, 7 * worked - target_scaled)
        deviations.append(deviation)

    return deviations


def _solve_lexicographic(
    model: cp_model.CpModel,
    x: dict,
    objectives: list[tuple[str, object]],
    started: float,
    total_budget: float,
) -> tuple[dict | None, str]:
    """目的式を優先順位順に最小化する.

    各段階の最適値を等式で固定してから次へ進む。
    時間上限は「全段階の合計」として扱い、各段階には残り時間を渡す
    （段階ごとに満額を与えると段階数に比例して増えてしまうため）。
    途中の段階が時間内に解けなかった場合は、直前に得られた解を返す
    （最適化の深さだけが浅くなり、結果は返る）。
    """
    values: dict | None = None
    status_name = SOLVER_STATUS_UNKNOWN

    if not objectives:
        # 最小化する対象がない（スタッフ0名など）。充足解だけを求める
        solver = _make_solver(total_budget)
        status = solver.Solve(model)
        status_name = _SOLVER_STATUS_BY_CODE.get(status, SOLVER_STATUS_UNKNOWN)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return _snapshot(solver, x), status_name
        return None, status_name

    for index, (_name, build_objective) in enumerate(objectives):
        remaining = total_budget - (time.monotonic() - started)
        if remaining <= 0:
            break

        objective = build_objective()
        model.Minimize(objective)
        solver = _make_solver(remaining)
        status = solver.Solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if values is not None:
                break
            return None, _SOLVER_STATUS_BY_CODE.get(status, SOLVER_STATUS_UNKNOWN)

        values = _snapshot(solver, x)
        status_name = _SOLVER_STATUS_BY_CODE.get(status, SOLVER_STATUS_UNKNOWN)

        # 次の段階のため、この段階の最適値を固定する
        if index + 1 < len(objectives):
            model.Add(objective == int(round(solver.ObjectiveValue())))

    return values, status_name


def _snapshot(solver: cp_model.CpSolver, x: dict) -> dict:
    """この解の x[s,d] を取り出す（後続の段階が失敗しても使えるようにする）."""
    return {key: int(bool(solver.Value(variable))) for key, variable in x.items()}


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
    values: dict,
    staff_list: list[GenerationStaff],
    work_dates: list[str],
) -> list[GeneratedAssignment]:
    """出勤・休みの両方を返す. 勤務時刻は StaffDayCondition の実効時間を使う."""
    assignments: list[GeneratedAssignment] = []
    for work_date in work_dates:
        for staff in staff_list:
            is_working = bool(values.get((staff.staff_id, work_date), 0))
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


def _build_staff_summaries(
    values: dict,
    staff_list: list[GenerationStaff],
    work_dates: list[str],
) -> list[StaffGenerationSummary]:
    """スタッフ別の勤務状況（希望休の尊重・目標勤務日数への近さ）.

    希望休に出勤したことは制約違反ではないため issue にはせず、ここで集計して見せる。
    """
    period_days = len(work_dates)
    summaries: list[StaffGenerationSummary] = []

    for staff in staff_list:
        scheduled_days = sum(
            int(bool(values.get((staff.staff_id, work_date), 0))) for work_date in work_dates
        )
        requested = [work_date for work_date in work_dates if _wants_off(staff, work_date)]
        worked_on_requested = sum(
            1 for work_date in requested if values.get((staff.staff_id, work_date), 0)
        )

        target = staff.target_days_per_week
        if target is None:
            target_scaled = actual_scaled = deviation_scaled = None
        else:
            target_scaled = target * period_days
            actual_scaled = 7 * scheduled_days
            deviation_scaled = abs(actual_scaled - target_scaled)

        summaries.append(
            StaffGenerationSummary(
                staff_id=staff.staff_id,
                staff_name=staff.staff_name,
                scheduled_days=scheduled_days,
                period_days=period_days,
                target_days_per_week=target,
                target_scaled=target_scaled,
                actual_scaled=actual_scaled,
                deviation_scaled=deviation_scaled,
                prefer_off_requested_count=len(requested),
                prefer_off_worked_count=worked_on_requested,
            )
        )

    return summaries


def _build_daily_results(
    values: dict,
    staff_list: list[GenerationStaff],
    days: list[GenerationDay],
    candidates: dict[int, set[str]],
) -> tuple[list[DailyGenerationResult], list[GenerationIssue]]:
    """日別の配置人数と不足を求める.

    不足はSolverの不足変数を読まず、配置結果と需要から再計算する
    （段階の途中で予算切れになった場合でも、返す解と不足が必ず一致するようにするため）。
    """
    results: list[DailyGenerationResult] = []
    issues: list[GenerationIssue] = []

    for day in days:
        work_date = day.work_date
        working = [
            staff for staff in staff_list if values.get((staff.staff_id, work_date), 0)
        ]
        scheduled = len(working)
        excluded = sum(
            1 for staff in staff_list if work_date not in candidates.get(staff.staff_id, set())
        )

        if not day.requirement_is_set:
            staff_shortage = skill_shortage = 0
            role_shortages: dict[int, int] = {}
        else:
            staff_shortage = max(0, day.required_total_staff - scheduled)
            skill_shortage = 0
            if day.required_skill_count > 0 and day.required_skill_level is not None:
                skilled = sum(
                    1 for staff in working if staff.skill_level >= day.required_skill_level
                )
                skill_shortage = max(0, day.required_skill_count - skilled)
            role_shortages = {}
            for role_id, required_count in day.role_requirements.items():
                if required_count <= 0:
                    continue
                placed = sum(1 for staff in working if staff.role_id == role_id)
                missing = max(0, required_count - placed)
                if missing > 0:
                    role_shortages[role_id] = missing

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
