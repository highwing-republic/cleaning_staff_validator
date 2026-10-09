"""CP-SAT によるシフト生成のテスト（DB非依存）.

指示書のS01〜S22に対応するテストには対応番号をコメントで示す。
Phase 9で公平性を入れるまでは、同条件のスタッフのどちらが選ばれるかは検証しない
（不足数・配置人数・制約充足だけを見る）。
"""

import pytest

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
    SOLVER_STATUS_OPTIMAL,
    ROLE_CHECKER,
    ROLE_CLEANER,
    ROLE_LEADER,
)
from src.day_conditions import resolve_period_conditions
from src.models import (
    GenerationDay,
    GenerationRequest,
    GenerationStaff,
    StaffDatePreferenceInput,
    StaffDetail,
    StaffInput,
)
from src.period_utils import period_dates
from src.shift_generation import generate_shift

LEADER, CHECKER, CLEANER = 1, 2, 3

# 2026-10-20(火) 〜 2026-10-24(土)
DATES = period_dates("2026-10-20", 5)
TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY = DATES
EVERY_WEEKDAY = (0, 1, 2, 3, 4, 5, 6)


def make_staff(
    staff_id: int,
    staff_name: str | None = None,
    *,
    role_id: int = CLEANER,
    role_code: str | None = None,
    skill_level: int = 3,
    standard_start_time: str | None = "09:00",
    standard_end_time: str | None = "15:30",
    weekdays: tuple[int, ...] = EVERY_WEEKDAY,
    preferences: dict[str, StaffDatePreferenceInput] | None = None,
    max_consecutive_days: int | None = None,
    target_days_per_week: int | None = None,
    work_dates: list[str] | None = None,
) -> GenerationStaff:
    """通常条件と勤務希望から day_conditions を作ったSolver入力を返す."""
    dates = work_dates or DATES
    name = staff_name or f"S{staff_id}"
    detail = StaffDetail(
        staff=StaffInput(
            staff_id, f"{staff_id:04d}", name, role_id, skill_level, "清掃", True,
            standard_start_time, standard_end_time,
        ),
        weekdays=weekdays,
    )
    conditions = {
        c.work_date: c
        for c in resolve_period_conditions(detail, dates, preferences or {})
    }
    return GenerationStaff(
        staff_id=staff_id,
        employee_code=f"{staff_id:04d}",
        staff_name=name,
        role_id=role_id,
        skill_level=skill_level,
        role_code=role_code or {
            LEADER: ROLE_LEADER,
            CHECKER: ROLE_CHECKER,
            CLEANER: ROLE_CLEANER,
        }.get(role_id),
        day_conditions=conditions,
        max_consecutive_days=max_consecutive_days,
        target_days_per_week=target_days_per_week,
    )


def pref(staff_id, work_date, **kwargs) -> StaffDatePreferenceInput:
    return StaffDatePreferenceInput(staff_id=staff_id, work_date=work_date, **kwargs)


def day(work_date, **kwargs) -> GenerationDay:
    return GenerationDay(work_date=work_date, requirement_is_set=True, **kwargs)


def required(count, dates=None) -> list[GenerationDay]:
    return [day(d, required_total_staff=count) for d in (dates or DATES)]


def run(staff, days, work_dates=None, **kwargs):
    return generate_shift(
        GenerationRequest(
            work_dates=work_dates or DATES, staff=staff, days=days, **kwargs
        )
    )


def worked_dates(result, staff_id) -> set[str]:
    return {a.work_date for a in result.working_assignments(staff_id)}


# ---------------------------------------------------------------------------
# S01〜S04: 基本
# ---------------------------------------------------------------------------


def test_s01_places_everyone_needed_without_shortage():
    """S01: 必要3・候補3 → 3名配置・不足0."""
    result = run([make_staff(i) for i in (1, 2, 3)], required(3))
    assert result.solver_status == SOLVER_STATUS_OPTIMAL
    assert [d.scheduled_staff_count for d in result.days] == [3] * len(DATES)
    assert result.total_shortage == 0
    assert all(d.status == GENERATION_STATUS_OK for d in result.days)


def test_s02_returns_a_plan_even_when_short_handed():
    """S02: 必要3・候補2 → 2名配置・不足1。結果は返る（作成不能にしない）."""
    result = run([make_staff(1), make_staff(2)], required(3))
    assert result.has_solution
    assert [d.scheduled_staff_count for d in result.days] == [2] * len(DATES)
    assert [d.staff_shortage for d in result.days] == [1] * len(DATES)
    assert all(d.status == GENERATION_STATUS_SHORTAGE for d in result.days)
    assert len(result.issues_with_code(GENERATION_ISSUE_STAFF_SHORTAGE)) == len(DATES)


def test_s03_zero_required_places_nobody():
    """S03: 必要0 → 0名配置（設定済み・必要0は有効な設定）."""
    result = run([make_staff(1), make_staff(2)], required(0))
    assert [d.scheduled_staff_count for d in result.days] == [0] * len(DATES)
    assert result.total_shortage == 0
    assert all(d.status == GENERATION_STATUS_OK for d in result.days)
    assert all(d.requirement_is_set for d in result.days)


def test_s04_requirement_missing_days_place_nobody():
    """S04: 要件未設定 → 0名配置・REQUIREMENT_MISSING."""
    result = run([make_staff(1)], [GenerationDay(work_date=d) for d in DATES])
    assert [d.scheduled_staff_count for d in result.days] == [0] * len(DATES)
    assert all(d.status == GENERATION_STATUS_REQUIREMENT_MISSING for d in result.days)
    assert all(d.required_total_staff is None for d in result.days)
    assert result.total_shortage == 0
    assert len(result.issues_with_code(GENERATION_ISSUE_REQUIREMENT_MISSING)) == len(DATES)


def test_requirement_missing_day_mixed_with_configured_days():
    days = [day(TUESDAY, required_total_staff=1), GenerationDay(work_date=WEDNESDAY)]
    result = run([make_staff(1)], days, work_dates=[TUESDAY, WEDNESDAY])
    by_date = {d.work_date: d for d in result.days}
    assert by_date[TUESDAY].scheduled_staff_count == 1
    assert by_date[WEDNESDAY].scheduled_staff_count == 0
    assert by_date[WEDNESDAY].status == GENERATION_STATUS_REQUIREMENT_MISSING


# ---------------------------------------------------------------------------
# S05〜S09: 勤務可能性
# ---------------------------------------------------------------------------


def test_s05_normal_weekday_is_a_candidate():
    """S05: 通常勤務曜日 → 候補."""
    result = run([make_staff(1, weekdays=(1,))], required(1))   # 火曜のみ
    assert TUESDAY in worked_dates(result, 1)


def test_s06_normal_off_weekday_is_not_a_candidate():
    """S06: 通常休み曜日 → 非候補."""
    result = run([make_staff(1, weekdays=(1,))], required(1))
    assert worked_dates(result, 1) == {TUESDAY}
    assert WEDNESDAY not in worked_dates(result, 1)


def test_s07_available_extra_makes_a_normal_off_day_a_candidate():
    """S07: 通常休み曜日 + AVAILABLE_EXTRA → 候補."""
    staff = make_staff(
        1, weekdays=(1,), preferences={WEDNESDAY: pref(1, WEDNESDAY, available_extra=True)}
    )
    result = run([staff], required(1))
    assert WEDNESDAY in worked_dates(result, 1)


def test_available_extra_does_not_force_working():
    """AVAILABLE_EXTRA は出勤を強制しない（必要人数が足りていれば休みのまま）."""
    staff = make_staff(
        1, weekdays=(1,), preferences={WEDNESDAY: pref(1, WEDNESDAY, available_extra=True)}
    )
    result = run([staff], required(0))
    assert worked_dates(result, 1) == set()


def test_s08_absolute_off_is_not_a_candidate():
    """S08: ABSOLUTE_OFF → 非候補."""
    staff = make_staff(1, preferences={TUESDAY: pref(1, TUESDAY, absolute_off=True)})
    result = run([staff], required(1))
    assert TUESDAY not in worked_dates(result, 1)
    by_date = {d.work_date: d for d in result.days}
    assert by_date[TUESDAY].staff_shortage == 1


def test_s09_prefer_off_is_still_a_candidate_in_phase8():
    """S09: PREFER_OFF → Phase 8では候補（目的関数には使わない）."""
    staff = make_staff(1, preferences={TUESDAY: pref(1, TUESDAY, prefer_off=True)})
    result = run([staff], required(1))
    assert TUESDAY in worked_dates(result, 1)


def test_absolute_off_with_prefer_off_is_not_a_candidate():
    """ABSOLUTE_OFF + PREFER_OFF の併用でも勤務不可が優先される（Phase 6仕様）."""
    staff = make_staff(
        1, preferences={TUESDAY: pref(1, TUESDAY, absolute_off=True, prefer_off=True)}
    )
    result = run([staff], required(1))
    assert TUESDAY not in worked_dates(result, 1)


def test_staff_without_weekday_pattern_is_never_scheduled():
    result = run([make_staff(1, weekdays=())], required(1))
    assert worked_dates(result, 1) == set()
    assert result.total_shortage == len(DATES)


# ---------------------------------------------------------------------------
# S10〜S13: 勤務時刻
# ---------------------------------------------------------------------------


def _times(result, staff_id) -> dict[str, tuple[str | None, str | None]]:
    return {
        a.work_date: (a.start_time, a.end_time)
        for a in result.working_assignments(staff_id)
    }


def test_s10_uses_standard_work_time():
    """S10: 通常09:00-15:30 → 生成後もその時間."""
    result = run([make_staff(1)], required(1))
    assert _times(result, 1)[TUESDAY] == ("09:00", "15:30")


def test_s11_early_leave_is_reflected():
    """S11: EARLY_LEAVE 13:00 → 09:00-13:00."""
    staff = make_staff(1, preferences={TUESDAY: pref(1, TUESDAY, override_end_time="13:00")})
    result = run([staff], required(1))
    assert _times(result, 1)[TUESDAY] == ("09:00", "13:00")


def test_s12_late_start_is_reflected():
    """S12: LATE_START 10:00 → 10:00-15:30."""
    staff = make_staff(1, preferences={TUESDAY: pref(1, TUESDAY, override_start_time="10:00")})
    result = run([staff], required(1))
    assert _times(result, 1)[TUESDAY] == ("10:00", "15:30")


def test_both_time_overrides_are_reflected():
    staff = make_staff(
        1,
        preferences={
            TUESDAY: pref(1, TUESDAY, override_start_time="10:00", override_end_time="13:00")
        },
    )
    result = run([staff], required(1))
    assert _times(result, 1)[TUESDAY] == ("10:00", "13:00")


def test_short_time_part_timer_keeps_their_own_hours():
    staff = make_staff(1, standard_end_time="13:00")
    result = run([staff], required(1))
    assert _times(result, 1)[TUESDAY] == ("09:00", "13:00")


def test_s13_unset_work_time_is_excluded_with_an_issue():
    """S13: time_status UNSET → 候補から除外し、issueを残す（生成は続行）."""
    staff = make_staff(1, standard_start_time=None, standard_end_time=None)
    result = run([staff], required(1))
    assert worked_dates(result, 1) == set()
    assert len(result.issues_with_code(GENERATION_ISSUE_UNSET_WORK_TIME)) == len(DATES)
    assert [d.staff_shortage for d in result.days] == [1] * len(DATES)
    assert result.days[0].excluded_staff_count == 1


def test_unset_work_time_does_not_stop_other_staff():
    """時間未設定のスタッフがいても他スタッフで生成を続けること."""
    staff = [
        make_staff(1, standard_start_time=None, standard_end_time=None),
        make_staff(2),
    ]
    result = run(staff, required(1))
    assert result.total_shortage == 0
    assert worked_dates(result, 2) == set(DATES)


def test_invalid_work_time_is_excluded_with_an_issue():
    """実効時間が成立しない日は候補から外す（勝手な時刻で勤務させない）."""
    staff = make_staff(1, preferences={TUESDAY: pref(1, TUESDAY, override_start_time="16:00")})
    result = run([staff], required(1))
    assert TUESDAY not in worked_dates(result, 1)
    assert len(result.issues_with_code(GENERATION_ISSUE_INVALID_WORK_TIME)) == 1


def test_days_off_have_no_times():
    result = run([make_staff(1)], required(0))
    off = [a for a in result.assignments if not a.is_working]
    assert off
    assert all(a.start_time is None and a.end_time is None for a in off)


def test_assignments_cover_every_staff_and_day():
    staff = [make_staff(1), make_staff(2)]
    result = run(staff, required(1))
    assert len(result.assignments) == 2 * len(DATES)


# ---------------------------------------------------------------------------
# S14〜S15: Role
# ---------------------------------------------------------------------------


def test_s14_role_requirement_met_has_no_shortage():
    """S14: Leader必要1・Leader候補あり → 不足0."""
    staff = [make_staff(1, role_id=LEADER), make_staff(2, role_id=CLEANER)]
    days = [day(d, required_total_staff=2, role_requirements={LEADER: 1}) for d in DATES]
    result = run(staff, days)
    assert result.total_shortage == 0
    assert all(d.role_shortages == {} for d in result.days)
    for work_date in DATES:
        assert work_date in worked_dates(result, 1)


def test_s15_role_shortage_is_reported_and_other_shifts_are_generated():
    """S15: Leader必要1・Leader候補なし → role_shortage1、他の配置は行う."""
    staff = [make_staff(1, role_id=CLEANER), make_staff(2, role_id=CLEANER)]
    days = [day(d, required_total_staff=2, role_requirements={LEADER: 1}) for d in DATES]
    result = run(staff, days)
    assert [d.role_shortages for d in result.days] == [{LEADER: 1}] * len(DATES)
    assert [d.scheduled_staff_count for d in result.days] == [2] * len(DATES)
    assert [d.staff_shortage for d in result.days] == [0] * len(DATES)
    assert len(result.issues_with_code(GENERATION_ISSUE_ROLE_SHORTAGE)) == len(DATES)


def test_role_requirement_zero_is_not_a_condition():
    staff = [make_staff(1, role_id=CLEANER)]
    days = [day(d, required_total_staff=1, role_requirements={LEADER: 0}) for d in DATES]
    result = run(staff, days)
    assert result.total_shortage == 0
    assert all(d.role_shortages == {} for d in result.days)


def test_s74_role_added_later_works_without_hard_coded_codes():
    """新しいRoleをDBへ追加しても固定コードなしでSolver条件に使えること."""
    new_role_id = 99
    staff = [make_staff(1, role_id=new_role_id), make_staff(2, role_id=CLEANER)]
    days = [day(d, required_total_staff=1, role_requirements={new_role_id: 1}) for d in DATES]
    result = run(staff, days)
    assert result.total_shortage == 0
    for work_date in DATES:
        assert work_date in worked_dates(result, 1)


def test_role_shortage_only_counts_matching_role():
    """1スタッフ1Roleの現行仕様（別Roleの出勤はRole不足を埋めない）."""
    staff = [make_staff(1, role_id=CHECKER)]
    days = [day(d, required_total_staff=1, role_requirements={LEADER: 1}) for d in DATES]
    result = run(staff, days)
    assert all(d.role_shortages == {LEADER: 1} for d in result.days)


# ---------------------------------------------------------------------------
# S16〜S17: Skill
# ---------------------------------------------------------------------------


def test_s16_skill_requirement_met_has_no_shortage():
    """S16: Skill4+必要2・候補2 → 不足0."""
    staff = [make_staff(1, skill_level=4), make_staff(2, skill_level=5), make_staff(3, skill_level=2)]
    days = [
        day(d, required_total_staff=2, required_skill_level=4, required_skill_count=2)
        for d in DATES
    ]
    result = run(staff, days)
    assert result.total_shortage == 0
    assert all(d.skill_shortage == 0 for d in result.days)


def test_s17_skill_shortage_is_reported():
    """S17: Skill4+候補1 → skill_shortage1."""
    staff = [make_staff(1, skill_level=4), make_staff(2, skill_level=2)]
    days = [
        day(d, required_total_staff=2, required_skill_level=4, required_skill_count=2)
        for d in DATES
    ]
    result = run(staff, days)
    assert [d.skill_shortage for d in result.days] == [1] * len(DATES)
    assert [d.scheduled_staff_count for d in result.days] == [2] * len(DATES)
    assert len(result.issues_with_code(GENERATION_ISSUE_SKILL_SHORTAGE)) == len(DATES)


def test_skill_count_zero_is_not_a_condition():
    staff = [make_staff(1, skill_level=1)]
    days = [day(d, required_total_staff=1, required_skill_level=5, required_skill_count=0) for d in DATES]
    result = run(staff, days)
    assert result.total_shortage == 0


def test_skill_condition_uses_level_or_above():
    staff = [make_staff(1, skill_level=5)]
    days = [day(d, required_total_staff=1, required_skill_level=3, required_skill_count=1) for d in DATES]
    result = run(staff, days)
    assert all(d.skill_shortage == 0 for d in result.days)


# ---------------------------------------------------------------------------
# S18: max_total_staff（Hard Constraint）
# ---------------------------------------------------------------------------


def test_s18_max_total_staff_is_respected():
    """S18: minimum3・max3・候補5 → 3名以下."""
    staff = [make_staff(i) for i in range(1, 6)]
    days = [day(d, required_total_staff=3, max_total_staff=3) for d in DATES]
    result = run(staff, days)
    assert [d.scheduled_staff_count for d in result.days] == [3] * len(DATES)
    assert result.total_shortage == 0


def test_max_total_staff_can_force_a_shortage():
    """最大人数がHardなので、必要人数を満たせない場合は不足として現れる."""
    staff = [make_staff(i) for i in range(1, 6)]
    days = [day(d, required_total_staff=4, max_total_staff=2) for d in DATES]
    result = run(staff, days)
    assert [d.scheduled_staff_count for d in result.days] == [2] * len(DATES)
    assert [d.staff_shortage for d in result.days] == [2] * len(DATES)


def test_max_total_staff_zero_places_nobody():
    staff = [make_staff(1)]
    days = [day(d, required_total_staff=0, max_total_staff=0) for d in DATES]
    result = run(staff, days)
    assert [d.scheduled_staff_count for d in result.days] == [0] * len(DATES)


# ---------------------------------------------------------------------------
# S19〜S20: 最大連続勤務日数
# ---------------------------------------------------------------------------


def test_s19_max_consecutive_days_is_respected():
    """S19: max連勤2・3日連続要件 → 同一staffを3連勤させない."""
    staff = [make_staff(i, max_consecutive_days=2) for i in (1, 2, 3)]
    result = run(staff, required(1))
    assert result.total_shortage == 0
    for staff_id in (1, 2, 3):
        assert _max_consecutive_run(sorted(worked_dates(result, staff_id))) <= 2


def _max_consecutive_run(dates: list[str]) -> int:
    """昇順の日付リストから最長の連続勤務日数を数える."""
    from datetime import timedelta

    from src.period_utils import parse_date

    longest = current = 0
    previous = None
    for work_date in dates:
        day_value = parse_date(work_date)
        if previous is not None and day_value - previous == timedelta(days=1):
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = day_value
    return longest


def test_max_consecutive_days_creates_shortage_when_unavoidable():
    """1人しかいなければ連勤上限のために不足が出る（上限を破らない）."""
    result = run([make_staff(1, max_consecutive_days=2)], required(1))
    assert _max_consecutive_run(sorted(worked_dates(result, 1))) <= 2
    assert result.total_shortage > 0


def test_no_limit_when_max_consecutive_days_is_none():
    result = run([make_staff(1, max_consecutive_days=None)], required(1))
    assert worked_dates(result, 1) == set(DATES)


def test_s20_prior_work_history_counts_toward_consecutive_limit():
    """S20: 期間前2連勤済み・max連勤2 → 期間初日は勤務不可."""
    staff = [make_staff(1, max_consecutive_days=2)]
    prior = {1: {"2026-10-18", "2026-10-19"}}
    result = run(staff, required(1), prior_work_history=prior)
    assert TUESDAY not in worked_dates(result, 1)

    without_prior = run(staff, required(1))
    assert TUESDAY in worked_dates(without_prior, 1)


def test_prior_work_history_with_a_gap_does_not_restrict():
    """直前に休みが挟まっていれば連勤は途切れる."""
    staff = [make_staff(1, max_consecutive_days=2)]
    prior = {1: {"2026-10-17", "2026-10-18"}}   # 10/19 は休み
    result = run(staff, required(1), prior_work_history=prior)
    assert TUESDAY in worked_dates(result, 1)


def test_prior_work_history_of_one_day_allows_one_more():
    staff = [make_staff(1, max_consecutive_days=2)]
    prior = {1: {"2026-10-19"}}
    result = run(staff, required(1), prior_work_history=prior)
    worked = worked_dates(result, 1)
    assert len(worked) == 3
    assert not ({TUESDAY, WEDNESDAY} <= worked)
    assert result.total_shortage == 2


def test_prior_work_history_is_ignored_for_other_staff():
    staff = [make_staff(1, max_consecutive_days=2), make_staff(2, max_consecutive_days=2)]
    prior = {1: {"2026-10-18", "2026-10-19"}}
    result = run(staff, required(1), prior_work_history=prior)
    assert TUESDAY not in worked_dates(result, 1)
    assert TUESDAY in worked_dates(result, 2)


# ---------------------------------------------------------------------------
# S21〜S22: 二段階最適化と重複不足
# ---------------------------------------------------------------------------


def test_s21_does_not_schedule_everyone_when_fewer_are_needed():
    """S21: 必要2・候補5 → 2名だけ配置（全員出勤にしない）."""
    staff = [make_staff(i) for i in range(1, 6)]
    result = run(staff, required(2))
    assert result.total_shortage == 0
    assert [d.scheduled_staff_count for d in result.days] == [2] * len(DATES)
    assert result.total_workdays == 2 * len(DATES)


def test_shortage_is_minimized_before_workdays():
    """不足の最小化が出勤日数の最小化より優先されること."""
    staff = [make_staff(i) for i in range(1, 4)]
    result = run(staff, required(3))
    assert result.total_shortage == 0
    assert [d.scheduled_staff_count for d in result.days] == [3] * len(DATES)


def test_s22_overlapping_shortages_are_both_recorded():
    """S22: 必要2・Leader1必要・候補1（非Leader） → 人数不足1とRole不足1の両方."""
    staff = [make_staff(1, role_id=CLEANER)]
    days = [day(d, required_total_staff=2, role_requirements={LEADER: 1}) for d in DATES]
    result = run(staff, days)
    for result_day in result.days:
        assert result_day.staff_shortage == 1
        assert result_day.role_shortages == {LEADER: 1}
        assert result_day.total_shortage == 2


# ---------------------------------------------------------------------------
# S75: 決定性
# ---------------------------------------------------------------------------


def test_s75_same_input_gives_the_same_shortage_and_counts():
    """同じ入力なら不足数・配置人数・制約充足が安定すること.

    どのスタッフが選ばれるかは検証しない（公平性はPhase 9で扱う）。
    """
    def build():
        staff = [make_staff(i, skill_level=3 + (i % 2)) for i in range(1, 6)]
        days = [
            day(d, required_total_staff=3, role_requirements={CLEANER: 2},
                required_skill_level=4, required_skill_count=1)
            for d in DATES
        ]
        return staff, days

    results = [run(*build()) for _ in range(3)]
    assert len({r.total_shortage for r in results}) == 1
    assert len({r.total_workdays for r in results}) == 1
    assert len({tuple(d.scheduled_staff_count for d in r.days) for r in results}) == 1
    assert len({r.solver_status for r in results}) == 1


# ---------------------------------------------------------------------------
# 周辺
# ---------------------------------------------------------------------------


def test_empty_period_returns_empty_result():
    result = generate_shift(GenerationRequest(work_dates=[], staff=[], days=[]))
    assert result.days == []
    assert result.assignments == []
    assert not result.has_solution


def test_no_staff_returns_shortage_without_failing():
    result = run([], required(2))
    assert result.has_solution
    assert [d.staff_shortage for d in result.days] == [2] * len(DATES)
    assert result.assignments == []


def test_missing_generation_day_is_treated_as_requirement_missing():
    """days に無い日付は要件未設定として扱う."""
    result = run([make_staff(1)], [day(TUESDAY, required_total_staff=1)])
    by_date = {d.work_date: d for d in result.days}
    assert by_date[WEDNESDAY].status == GENERATION_STATUS_REQUIREMENT_MISSING
    assert len(result.days) == len(DATES)


def test_generation_crosses_month_boundary():
    dates = period_dates("2026-10-28", 10)
    staff = [make_staff(1, work_dates=dates)]
    days = [day(d, required_total_staff=1) for d in dates]
    result = run(staff, days, work_dates=dates)
    assert [d.work_date for d in result.days] == dates
    assert worked_dates(result, 1) == set(dates)
    assert any(d.startswith("2026-11") for d in dates)


def test_fourteen_day_period_with_twenty_staff_solves_quickly():
    dates = period_dates("2026-10-20", 14)
    staff = [make_staff(i, work_dates=dates) for i in range(1, 21)]
    days = [
        day(d, required_total_staff=6, role_requirements={CLEANER: 5},
            required_skill_level=3, required_skill_count=2)
        for d in dates
    ]
    result = run(staff, days, work_dates=dates)
    assert result.solver_status == SOLVER_STATUS_OPTIMAL
    assert result.total_shortage == 0
    assert all(d.scheduled_staff_count == 6 for d in result.days)
    assert result.solve_seconds < 10


def test_reserved_rooms_does_not_affect_the_plan():
    """予約室数はPhase 8の制約に使わない（必要人数は入力値をそのまま使う）."""
    staff = [make_staff(i) for i in (1, 2, 3)]
    plain = run(staff, [day(d, required_total_staff=2) for d in DATES])
    with_rooms = run(
        staff, [day(d, required_total_staff=2, reserved_rooms=30) for d in DATES]
    )
    assert [d.scheduled_staff_count for d in plain.days] == [
        d.scheduled_staff_count for d in with_rooms.days
    ]
    assert plain.total_shortage == with_rooms.total_shortage


@pytest.mark.parametrize("limit", [1.0, 5.0])
def test_time_limit_is_accepted(limit):
    result = run([make_staff(1)], required(1), time_limit_seconds=limit)
    assert result.has_solution


# ---------------------------------------------------------------------------
# fixed_assignments（Phase 10・DB非依存）
# ---------------------------------------------------------------------------


def test_fixed_assignment_forces_work():
    """固定出勤は総出勤日数を増やしてでも守る（Hard制約）."""
    staff = [make_staff(1), make_staff(2), make_staff(3)]
    result = run(staff, required(1), fixed_assignments={(3, THURSDAY): 1})
    assert result.solver_status == SOLVER_STATUS_OPTIMAL
    assert THURSDAY in worked_dates(result, 3)


def test_fixed_off_forces_rest():
    """固定休みは不足が出ても守る."""
    staff = [make_staff(1)]
    result = run(staff, required(1), fixed_assignments={(1, THURSDAY): 0})
    assert result.has_solution
    assert THURSDAY not in worked_dates(result, 1)
    by_date = {d.work_date: d for d in result.days}
    assert by_date[THURSDAY].staff_shortage == 1
    assert by_date[FRIDAY].staff_shortage == 0


def test_fixed_assignments_mixed_on_and_off():
    staff = [make_staff(1), make_staff(2)]
    result = run(
        staff,
        required(1),
        fixed_assignments={(1, TUESDAY): 1, (2, TUESDAY): 1, (1, WEDNESDAY): 0},
    )
    assert result.has_solution
    assert TUESDAY in worked_dates(result, 1)
    assert TUESDAY in worked_dates(result, 2)
    assert WEDNESDAY not in worked_dates(result, 1)


def test_fixed_assignment_overrides_prefer_off():
    """固定は公平性・希望休より優先する（Soft扱いにしない）."""
    staff = [
        make_staff(1, preferences={THURSDAY: pref(1, THURSDAY, prefer_off=True)}),
        make_staff(2),
    ]
    result = run(staff, required(1), fixed_assignments={(1, THURSDAY): 1})
    assert THURSDAY in worked_dates(result, 1)


def test_fixed_assignment_overrides_target_days():
    """固定出勤はtarget_days_per_weekより優先する."""
    staff = [make_staff(1, target_days_per_week=1), make_staff(2)]
    result = run(
        staff,
        required(1),
        fixed_assignments={(1, d): 1 for d in DATES},
    )
    assert worked_dates(result, 1) == set(DATES)


def test_fixed_work_on_unavailable_day_is_infeasible():
    """ABSOLUTE_OFFの日に出勤固定 → 解なし（固定を黙って外さない）."""
    staff = [
        make_staff(1, preferences={THURSDAY: pref(1, THURSDAY, absolute_off=True)}),
    ]
    result = run(staff, required(1), fixed_assignments={(1, THURSDAY): 1})
    assert not result.has_solution


def test_fixed_assignment_outside_the_period_is_ignored():
    staff = [make_staff(1)]
    result = run(staff, required(1), fixed_assignments={(1, "2026-11-30"): 0})
    assert result.has_solution
    assert worked_dates(result, 1) == set(DATES)


def test_fixed_assignment_for_unknown_staff_is_ignored():
    staff = [make_staff(1)]
    result = run(staff, required(1), fixed_assignments={(99, THURSDAY): 1})
    assert result.has_solution


def test_fixed_assignment_keeps_requirements_for_other_days():
    staff = [make_staff(1), make_staff(2)]
    result = run(staff, required(2), fixed_assignments={(1, TUESDAY): 1})
    assert result.total_shortage == 0
    assert all(d.scheduled_staff_count == 2 for d in result.days)


def test_no_fixed_assignments_matches_plain_generation():
    """固定なしなら従来の生成結果と同じ（Phase 9の挙動を変えない）."""
    staff = [make_staff(i) for i in (1, 2, 3)]
    plain = run(staff, required(2))
    empty = run(staff, required(2), fixed_assignments={})
    assert [d.scheduled_staff_count for d in plain.days] == [
        d.scheduled_staff_count for d in empty.days
    ]
    assert plain.total_shortage == empty.total_shortage


def test_a_zero_time_limit_is_honored_not_replaced_by_the_default():
    """time_limit_seconds=0 は既定の10秒に置き換えず、即座に打ち切る."""
    result = run([make_staff(1)], required(1), time_limit_seconds=0.0)
    assert not result.has_solution
    assert result.solve_seconds < 2
