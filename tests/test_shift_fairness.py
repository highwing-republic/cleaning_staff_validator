"""希望休の尊重と目標勤務日数による配置最適化のテスト（Phase 9）.

指示書のF01〜F13に対応するテストには対応番号をコメントで示す。
同じ目的値を持つスタッフのどちらが選ばれるかは検証しない（決定性のみ確認する）。
"""

import time

import pytest

from src.constants import (
    GENERATION_ISSUE_ROLE_SHORTAGE,
    GENERATION_ISSUE_SKILL_SHORTAGE,
    GENERATION_ISSUE_STAFF_SHORTAGE,
    SOLVER_STATUS_OPTIMAL,
)
from src.generation_display import (
    format_prefer_off_respect,
    format_scheduled_days,
    format_target_days,
)
from src.models import GenerationRequest
from src.period_utils import period_dates
from src.shift_generation import generate_shift
from tests.test_shift_generation import (
    CHECKER,
    CLEANER,
    DATES,
    LEADER,
    day,
    make_staff,
    pref,
    required,
    run,
    worked_dates,
)

TUESDAY = DATES[0]
DATES_10 = period_dates("2026-10-20", 10)
DATES_14 = period_dates("2026-10-20", 14)


def _max_consecutive_run(dates: list[str]) -> int:
    from datetime import timedelta

    from src.period_utils import parse_date

    longest = current = 0
    previous = None
    for work_date in sorted(dates):
        value = parse_date(work_date)
        current = current + 1 if previous and value - previous == timedelta(days=1) else 1
        longest = max(longest, current)
        previous = value
    return longest


# ---------------------------------------------------------------------------
# F01〜F04: PREFER_OFF と Phase 8 の性質
# ---------------------------------------------------------------------------


def test_f01_prefers_staff_without_a_day_off_request():
    """F01: 必要1・A通常・B希望休 → Aが勤務しBは休み."""
    a = make_staff(1, "A")
    b = make_staff(2, "B", preferences={d: pref(2, d, prefer_off=True) for d in DATES})
    result = run([a, b], required(1))

    assert result.total_shortage == 0
    assert worked_dates(result, 1) == set(DATES)
    assert worked_dates(result, 2) == set()
    assert result.prefer_off_worked_total == 0
    assert result.prefer_off_requested_total == len(DATES)


def test_f02_breaks_a_day_off_request_when_the_staff_is_needed():
    """F02: 必要2・A通常・B希望休 → 両方勤務・不足0・希望休違反あり."""
    a = make_staff(1, "A")
    b = make_staff(2, "B", preferences={d: pref(2, d, prefer_off=True) for d in DATES})
    result = run([a, b], required(2))

    assert result.total_shortage == 0
    assert all(d.scheduled_staff_count == 2 for d in result.days)
    assert result.prefer_off_worked_total == len(DATES)


def test_f03_shortage_minimisation_outranks_day_off_requests():
    """F03: 希望休を守って不足1ではなく、希望休を破って不足0を選ぶ."""
    a = make_staff(1, "A")
    b = make_staff(2, "B", preferences={TUESDAY: pref(2, TUESDAY, prefer_off=True)})
    days = [day(TUESDAY, required_total_staff=2)]
    result = run([a, b], days, work_dates=[TUESDAY])

    assert result.total_shortage == 0
    assert result.days[0].scheduled_staff_count == 2
    assert result.prefer_off_worked_total == 1
    assert result.issues_with_code(GENERATION_ISSUE_STAFF_SHORTAGE) == []


def test_f04_keeps_the_phase8_behaviour_of_not_overstaffing():
    """F04: 必要1・候補3（希望休なし） → 1名だけ勤務."""
    result = run([make_staff(i) for i in (1, 2, 3)], required(1))
    assert all(d.scheduled_staff_count == 1 for d in result.days)
    assert result.total_workdays == len(DATES)


def test_day_off_request_is_not_an_issue():
    """希望休の日に勤務したことは制約違反ではないのでissueにしない."""
    b = make_staff(1, "B", preferences={TUESDAY: pref(1, TUESDAY, prefer_off=True)})
    result = run([b], [day(TUESDAY, required_total_staff=1)], work_dates=[TUESDAY])

    assert result.prefer_off_worked_total == 1
    assert result.issues == []


def test_absolute_off_combined_with_prefer_off_is_still_respected():
    """ABSOLUTE_OFF + PREFER_OFF の併用時も勤務不可が優先される（Phase 6仕様）."""
    staff = make_staff(
        1, "A", preferences={TUESDAY: pref(1, TUESDAY, absolute_off=True, prefer_off=True)}
    )
    result = run([staff], [day(TUESDAY, required_total_staff=1)], work_dates=[TUESDAY])

    assert worked_dates(result, 1) == set()
    summary = result.staff_summary(1)
    assert summary.prefer_off_requested_count == 1
    assert summary.prefer_off_worked_count == 0


def test_prefer_off_counts_are_per_staff():
    a = make_staff(1, "A", preferences={d: pref(1, d, prefer_off=True) for d in DATES[:2]})
    b = make_staff(2, "B", preferences={DATES[0]: pref(2, DATES[0], prefer_off=True)})
    result = run([a, b], required(0))

    assert result.staff_summary(1).prefer_off_requested_count == 2
    assert result.staff_summary(2).prefer_off_requested_count == 1
    assert result.prefer_off_requested_total == 3
    assert result.prefer_off_respected_total == 3


# ---------------------------------------------------------------------------
# F05〜F07: target_days_per_week
# ---------------------------------------------------------------------------


def test_f05_distributes_workdays_towards_each_target():
    """F05: 14日・A target5・B target2、必要1名/日 → A約10日・B約4日."""
    a = make_staff(1, "A", target_days_per_week=5, work_dates=DATES_14)
    b = make_staff(2, "B", target_days_per_week=2, work_dates=DATES_14)
    days = [day(d, required_total_staff=1) for d in DATES_14]
    result = run([a, b], days, work_dates=DATES_14)

    assert result.total_workdays == len(DATES_14)
    assert result.total_target_deviation == 0
    assert result.staff_summary(1).scheduled_days == 10
    assert result.staff_summary(2).scheduled_days == 4


def test_f06_ten_day_period_uses_integer_scale_not_rounding():
    """F06: 10日・週3日 → 目安4.29日。4日勤務が5日勤務より乖離が小さい."""
    a = make_staff(1, "A", target_days_per_week=3, work_dates=DATES_10)
    b = make_staff(2, "B", work_dates=DATES_10)
    days = [day(d, required_total_staff=1) for d in DATES_10]
    result = run([a, b], days, work_dates=DATES_10)

    summary = result.staff_summary(1)
    assert summary.scheduled_days == 4
    assert summary.target_scaled == 30
    assert summary.actual_scaled == 28
    assert summary.deviation_scaled == 2
    assert summary.target_days == pytest.approx(30 / 7)


@pytest.mark.parametrize(
    ("scheduled_days", "expected_deviation"), [(3, 9), (4, 2), (5, 5), (6, 12)]
)
def test_deviation_is_smallest_at_four_days_for_ten_day_period(
    scheduled_days, expected_deviation
):
    """10日・週3日のとき各勤務日数の乖離（4日が最小であることの根拠）."""
    assert abs(7 * scheduled_days - 3 * 10) == expected_deviation


def test_f07_staff_without_a_target_is_out_of_scope():
    """F07: target_days_per_week=NULL → 乖離の対象外・生成は正常."""
    a = make_staff(1, "A", target_days_per_week=None)
    result = run([a], required(1))

    summary = result.staff_summary(1)
    assert summary.has_target is False
    assert summary.target_days_per_week is None
    assert summary.target_scaled is None
    assert summary.deviation_scaled is None
    assert result.total_target_deviation == 0
    assert result.solver_status == SOLVER_STATUS_OPTIMAL


def test_mixed_staff_with_and_without_targets():
    a = make_staff(1, "A", target_days_per_week=7, work_dates=DATES_14)
    b = make_staff(2, "B", work_dates=DATES_14)
    days = [day(d, required_total_staff=1) for d in DATES_14]
    result = run([a, b], days, work_dates=DATES_14)

    assert result.staff_summary(1).has_target is True
    assert result.staff_summary(2).has_target is False
    assert result.staff_summary(1).scheduled_days == len(DATES_14)


@pytest.mark.parametrize("target", [1, 2, 3, 4, 5, 6, 7])
def test_dynamic_target_values_all_work(target):
    """target_days は固定値前提にしない（1〜7で同じコードが動くこと）."""
    a = make_staff(1, "A", target_days_per_week=target, work_dates=DATES_14)
    b = make_staff(2, "B", work_dates=DATES_14)
    days = [day(d, required_total_staff=1) for d in DATES_14]
    result = run([a, b], days, work_dates=DATES_14)

    summary = result.staff_summary(1)
    assert summary.target_scaled == target * len(DATES_14)
    assert summary.scheduled_days == target * 2        # 14日 × target/7
    assert summary.deviation_scaled == 0


# ---------------------------------------------------------------------------
# F08〜F09, F13: target より上位の条件
# ---------------------------------------------------------------------------


def test_f08_availability_outranks_the_target():
    """F08: target5だが通常勤務可能日が3日 → 勤務可能な範囲で最適化する."""
    a = make_staff(1, "A", target_days_per_week=5, weekdays=(1, 2, 3))   # 火水木
    result = run([a], required(1))

    assert result.staff_summary(1).scheduled_days == 3
    assert worked_dates(result, 1) == {DATES[0], DATES[1], DATES[2]}


def test_f09_absolute_off_outranks_the_target():
    """F09: target7だがABSOLUTE_OFFあり → 絶対休みを守る."""
    a = make_staff(
        1, "A", target_days_per_week=7,
        preferences={TUESDAY: pref(1, TUESDAY, absolute_off=True)},
    )
    result = run([a], required(1))

    assert TUESDAY not in worked_dates(result, 1)
    assert result.staff_summary(1).scheduled_days == len(DATES) - 1


def test_f13_max_consecutive_days_outranks_the_target():
    """F13: targetへ近づけるために最大連勤を破らない."""
    a = make_staff(1, "A", target_days_per_week=7, max_consecutive_days=2)
    result = run([a], required(1))

    assert _max_consecutive_run(worked_dates(result, 1)) <= 2
    assert result.staff_summary(1).scheduled_days < len(DATES)


def test_target_does_not_create_overstaffing():
    """targetへ近づけるために必要以上の人数を出勤させない（総出勤日数の最小化が上位）."""
    staff = [make_staff(i, f"S{i}", target_days_per_week=7) for i in (1, 2, 3)]
    result = run(staff, required(1))

    assert all(d.scheduled_staff_count == 1 for d in result.days)
    assert result.total_workdays == len(DATES)


def test_target_is_not_a_hard_constraint_when_more_staff_are_needed():
    """週3日希望でも必要なら期間中ずっと勤務する."""
    a = make_staff(1, "A", target_days_per_week=3)
    result = run([a], required(1))

    assert result.staff_summary(1).scheduled_days == len(DATES)
    assert result.total_shortage == 0


def test_target_is_not_a_hard_constraint_when_demand_is_low():
    """需要が少なければ目標より少ない勤務日数になる."""
    a = make_staff(1, "A", target_days_per_week=7)
    days = [day(d, required_total_staff=1 if index < 2 else 0) for index, d in enumerate(DATES)]
    result = run([a], days)

    assert result.staff_summary(1).scheduled_days == 2


# ---------------------------------------------------------------------------
# F10: PREFER_OFF > target
# ---------------------------------------------------------------------------


def test_f10_day_off_request_outranks_the_target():
    """F10: 同じ総出勤日数なら、targetから少し外れても希望休を守る."""
    a = make_staff(
        1, "A", target_days_per_week=7,
        preferences={TUESDAY: pref(1, TUESDAY, prefer_off=True)},
    )
    b = make_staff(2, "B")
    result = run([a, b], required(1))

    assert TUESDAY not in worked_dates(result, 1)
    assert result.prefer_off_worked_total == 0
    # targetへ近づけるなら A が全日勤務のはずだが、希望休を優先している
    assert result.staff_summary(1).scheduled_days == len(DATES) - 1
    assert result.total_workdays == len(DATES)


# ---------------------------------------------------------------------------
# F11〜F12: 期間跨ぎ / Role・Skill との共存
# ---------------------------------------------------------------------------


def test_f11_target_uses_period_days_across_a_month_boundary():
    """F11: 月跨ぎ14日でも period_days=14 で換算する（月ごとにリセットしない）."""
    dates = period_dates("2026-10-28", 14)
    assert dates[0] == "2026-10-28"
    assert dates[-1] == "2026-11-10"

    a = make_staff(1, "A", target_days_per_week=3, work_dates=dates)
    b = make_staff(2, "B", work_dates=dates)
    days = [day(d, required_total_staff=1) for d in dates]
    result = run([a, b], days, work_dates=dates)

    summary = result.staff_summary(1)
    assert summary.period_days == 14
    assert summary.target_scaled == 3 * 14
    assert summary.scheduled_days == 6
    assert summary.deviation_scaled == 0


def test_f12_role_shortage_is_not_accepted_for_fairness():
    """F12: 公平性を優先してRole不足を選ばない（Pass 1が最優先）."""
    leader = make_staff(1, "L", role_id=LEADER, target_days_per_week=1)
    cleaner = make_staff(2, "C", role_id=CLEANER, target_days_per_week=7)
    days = [day(d, required_total_staff=1, role_requirements={LEADER: 1}) for d in DATES]
    result = run([leader, cleaner], days)

    assert result.total_shortage == 0
    assert result.issues_with_code(GENERATION_ISSUE_ROLE_SHORTAGE) == []
    # targetは1日だが、Role条件を満たすため全日勤務になる
    assert result.staff_summary(1).scheduled_days == len(DATES)


def test_skill_shortage_is_not_accepted_for_fairness():
    skilled = make_staff(1, "S", skill_level=5, target_days_per_week=1)
    plain = make_staff(2, "P", skill_level=2, target_days_per_week=7)
    days = [
        day(d, required_total_staff=1, required_skill_level=4, required_skill_count=1)
        for d in DATES
    ]
    result = run([skilled, plain], days)

    assert result.total_shortage == 0
    assert result.issues_with_code(GENERATION_ISSUE_SKILL_SHORTAGE) == []
    assert result.staff_summary(1).scheduled_days == len(DATES)


def test_day_off_request_is_not_accepted_over_a_role_shortage():
    """希望休を守るためにRole不足を許容しない."""
    leader = make_staff(
        1, "L", role_id=LEADER,
        preferences={TUESDAY: pref(1, TUESDAY, prefer_off=True)},
    )
    days = [day(TUESDAY, required_total_staff=1, role_requirements={LEADER: 1})]
    result = run([leader], days, work_dates=[TUESDAY])

    assert result.total_shortage == 0
    assert TUESDAY in worked_dates(result, 1)
    assert result.prefer_off_worked_total == 1


def test_max_total_staff_still_holds_with_fairness():
    staff = [make_staff(i, f"S{i}", target_days_per_week=7) for i in range(1, 6)]
    days = [day(d, required_total_staff=4, max_total_staff=2) for d in DATES]
    result = run(staff, days)

    assert all(d.scheduled_staff_count == 2 for d in result.days)
    assert all(d.staff_shortage == 2 for d in result.days)


# ---------------------------------------------------------------------------
# 性能・決定性
# ---------------------------------------------------------------------------


def _performance_request(staff_count: int):
    staff = []
    for index in range(1, staff_count + 1):
        off_day = DATES_14[(index * 3) % len(DATES_14)]
        staff.append(
            make_staff(
                index, f"S{index}",
                role_id=LEADER if index <= 3 else CLEANER,
                skill_level=3 + (index % 3),
                target_days_per_week=2 + (index % 4),
                max_consecutive_days=5,
                preferences={off_day: pref(index, off_day, prefer_off=True)},
                work_dates=DATES_14,
            )
        )
    days = [
        day(d, required_total_staff=6, role_requirements={LEADER: 1},
            required_skill_level=4, required_skill_count=2)
        for d in DATES_14
    ]
    return GenerationRequest(work_dates=DATES_14, staff=staff, days=days)


def test_twenty_staff_over_fourteen_days_solves_quickly_with_four_passes():
    started = time.monotonic()
    result = generate_shift(_performance_request(20))
    elapsed = time.monotonic() - started

    assert result.solver_status == SOLVER_STATUS_OPTIMAL
    assert result.total_shortage == 0
    assert all(d.scheduled_staff_count == 6 for d in result.days)
    assert elapsed < 10
    assert result.solve_seconds < 10


def test_four_passes_stay_within_the_total_time_budget():
    """時間上限は全段階の合計として扱う（段階ごとに満額を与えない）."""
    request = _performance_request(20)
    budget = 2.0
    bounded = GenerationRequest(
        work_dates=request.work_dates, staff=request.staff, days=request.days,
        time_limit_seconds=budget,
    )
    started = time.monotonic()
    result = generate_shift(bounded)
    elapsed = time.monotonic() - started

    assert result.has_solution
    assert elapsed < budget + 1.0


def test_same_input_gives_the_same_fairness_outcome():
    results = [generate_shift(_performance_request(20)) for _ in range(3)]

    assert len({r.total_shortage for r in results}) == 1
    assert len({r.total_workdays for r in results}) == 1
    assert len({r.prefer_off_worked_total for r in results}) == 1
    assert len({r.total_target_deviation for r in results}) == 1


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------


def test_target_days_display_converts_from_the_integer_scale():
    a = make_staff(1, "A", target_days_per_week=3, work_dates=DATES_14)
    result = run([a], [day(d, required_total_staff=0) for d in DATES_14], work_dates=DATES_14)
    assert format_target_days(result.staff_summary(1)) == "6.0日"


def test_target_days_display_for_a_ten_day_period():
    a = make_staff(1, "A", target_days_per_week=3, work_dates=DATES_10)
    result = run([a], [day(d, required_total_staff=0) for d in DATES_10], work_dates=DATES_10)
    assert format_target_days(result.staff_summary(1)) == "4.3日"


def test_target_days_display_without_a_target():
    result = run([make_staff(1, "A")], required(0))
    assert format_target_days(result.staff_summary(1)) == "-"


def test_scheduled_days_display():
    result = run([make_staff(1, "A")], required(1))
    assert format_scheduled_days(result.staff_summary(1)) == f"{len(DATES)}日"


def test_prefer_off_respect_display():
    a = make_staff(1, "A", preferences={d: pref(1, d, prefer_off=True) for d in DATES[:3]})
    b = make_staff(2, "B")
    result = run([a, b], required(1))
    assert format_prefer_off_respect(result) == "3 / 3"


def test_prefer_off_respect_display_when_some_are_broken():
    a = make_staff(1, "A", preferences={d: pref(1, d, prefer_off=True) for d in DATES})
    result = run([a], [day(DATES[0], required_total_staff=1)] + [
        day(d, required_total_staff=0) for d in DATES[1:]
    ])
    assert format_prefer_off_respect(result) == f"{len(DATES) - 1} / {len(DATES)}"


def test_prefer_off_respect_display_without_requests():
    result = run([make_staff(1, "A")], required(1))
    assert format_prefer_off_respect(result) == "-"
