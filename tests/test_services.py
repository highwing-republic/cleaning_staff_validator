"""T73/T74/T79〜T84: 生成・保存・手動変更・固定・再計算・確定・確定保護."""

import pytest

from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database
from src.models import (
    DailyRequirementInput,
    MonthlyConditionInput,
    PreferenceInput,
    RoleRequirementInput,
)
from src.month_utils import get_month_dates

YM = "2026-02"
DATES = get_month_dates(YM)
LEADER, CLEANER = 1, 3


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


def _setup_month(conn, *, required=1, n_staff=3, target_days=10):
    ids = [
        repo.create_staff(conn, f"S{i}", LEADER if i == 1 else CLEANER, 480, 5)
        for i in range(1, n_staff + 1)
    ]
    for sid in ids:
        repo.save_monthly_condition(conn, MonthlyConditionInput(sid, YM, target_days * 480))
    repo.save_daily_requirements(conn, [DailyRequirementInput(d, required) for d in DATES])
    return ids


def _cell(conn, staff_id, work_date):
    for a in repo.load_assignments(conn, YM):
        if (a.staff_id, a.work_date) == (staff_id, work_date):
            return a
    return None


# ---------------------------------------------------------------------------
# T73 / T74 生成・保存
# ---------------------------------------------------------------------------


def test_generate_and_save(conn):
    ids = _setup_month(conn)
    outcome = services.generate_and_save(conn, YM)
    assert outcome.precheck.is_ok
    assert outcome.result.status == "OPTIMAL"
    assert outcome.saved
    assert len(repo.load_assignments(conn, YM)) == len(ids) * len(DATES)
    month = repo.get_schedule_month(conn, YM)
    assert month.status == "DRAFT"
    assert month.solver_status == "OPTIMAL"
    # 目標 3名x10日=30 > 最低人数 1x28=28 → 最低人数を超える出勤2（v1.4）
    assert month.objective_target_deviation == 0
    assert month.objective_overstaff == 2
    assert month.objective_max_overstaff == 1
    assert services.validate_current_schedule(conn, YM) == []


def test_precheck_failure_skips_solver_and_keeps_data(conn):
    _setup_month(conn)
    services.generate_and_save(conn, YM)
    before = repo.load_assignments(conn, YM)

    repo.save_daily_requirement(conn, DailyRequirementInput(DATES[5], 9))
    outcome = services.generate_and_save(conn, YM)
    assert not outcome.precheck.is_ok
    assert [e.code for e in outcome.precheck.errors] == ["PC05"]
    assert outcome.result is None
    assert not outcome.saved
    assert repo.load_assignments(conn, YM) == before


def test_solver_infeasible_keeps_existing_data(conn):
    ids = _setup_month(conn)
    services.generate_and_save(conn, YM)
    before = repo.load_assignments(conn, YM)
    before_month = repo.get_schedule_month(conn, YM)

    # 事前チェックでは検出できない不成立: 全員の最大勤務時間の合計 < 必要延べ人数
    for sid in ids:
        repo.save_monthly_condition(conn, MonthlyConditionInput(sid, YM, 9 * 480, None, 9 * 480))
    outcome = services.generate_and_save(conn, YM)
    assert outcome.precheck.is_ok
    assert outcome.result.status == "INFEASIBLE"
    assert not outcome.saved
    assert repo.load_assignments(conn, YM) == before
    assert repo.get_schedule_month(conn, YM) == before_month


def test_generation_uses_preferences_and_roles(conn):
    ids = _setup_month(conn)
    repo.save_role_requirements(conn, [RoleRequirementInput(d, LEADER, 1) for d in DATES[:3]])
    repo.save_preference(conn, PreferenceInput(ids[1], DATES[0], "UNAVAILABLE"))
    services.generate_and_save(conn, YM)
    for d in DATES[:3]:
        assert _cell(conn, ids[0], d).is_working
    assert not _cell(conn, ids[1], DATES[0]).is_working


# ---------------------------------------------------------------------------
# T79〜T82 手動変更・固定・再計算
# ---------------------------------------------------------------------------


def test_manual_edit_requires_generated_schedule(conn):
    ids = _setup_month(conn)
    with pytest.raises(ValueError):
        services.apply_manual_edit(conn, ids[0], DATES[0], True)


def test_manual_edit_returns_violations_and_is_not_locked(conn):
    ids = _setup_month(conn)
    services.generate_and_save(conn, YM)
    worker = next(a for a in repo.load_assignments(conn, YM) if a.is_working and a.work_date == DATES[3])

    errors = services.apply_manual_edit(conn, worker.staff_id, DATES[3], False)
    cell = _cell(conn, worker.staff_id, DATES[3])
    assert cell.is_working is False
    assert cell.source == "MANUAL"
    assert cell.is_locked is False
    assert "HC03" in [e.code for e in errors]  # 必要人数不足になる


def test_regenerate_keeps_locks_and_overwrites_unlocked_manual(conn):
    ids = _setup_month(conn)
    services.generate_and_save(conn, YM)

    # 未LOCKの手動変更（2日目に全員出勤）
    for sid in ids:
        services.apply_manual_edit(conn, sid, DATES[1], True)
    # LOCKした手動変更（10日目 staff1 休み, 11日目 staff1 出勤）
    services.apply_manual_edit(conn, ids[0], DATES[9], False, is_locked=True)
    services.apply_manual_edit(conn, ids[0], DATES[10], True, is_locked=True)

    outcome = services.generate_and_save(conn, YM)
    assert outcome.saved

    locked10 = _cell(conn, ids[0], DATES[9])
    locked11 = _cell(conn, ids[0], DATES[10])
    assert (locked10.is_working, locked10.is_locked, locked10.source) == (False, True, "MANUAL")
    assert (locked11.is_working, locked11.is_locked, locked11.source) == (True, True, "MANUAL")

    day2 = [a for a in repo.load_assignments(conn, YM) if a.work_date == DATES[1]]
    assert sum(a.is_working for a in day2) <= 2  # 全員出勤の未LOCK手動変更は再計算で解消
    assert all(a.source == "OPTIMIZED" and not a.is_locked for a in day2)
    assert services.validate_current_schedule(conn, YM) == []


def test_lock_only_change(conn):
    ids = _setup_month(conn)
    services.generate_and_save(conn, YM)
    services.set_lock(conn, ids[2], DATES[0], True)
    assert _cell(conn, ids[2], DATES[0]).is_locked
    services.set_lock(conn, ids[2], DATES[0], False)
    assert not _cell(conn, ids[2], DATES[0]).is_locked


# ---------------------------------------------------------------------------
# T83 / T84 確定・確定保護
# ---------------------------------------------------------------------------


def test_confirm_rejected_with_hard_violation(conn):
    ids = _setup_month(conn)
    services.generate_and_save(conn, YM)
    worker = next(a for a in repo.load_assignments(conn, YM) if a.is_working and a.work_date == DATES[3])
    services.apply_manual_edit(conn, worker.staff_id, DATES[3], False)

    outcome = services.confirm_month(conn, YM)
    assert not outcome.confirmed
    assert "HC03" in [e.code for e in outcome.errors]
    assert not services.is_confirmed(conn, YM)


def test_confirm_success(conn):
    _setup_month(conn)
    services.generate_and_save(conn, YM)
    outcome = services.confirm_month(conn, YM)
    assert outcome.confirmed and outcome.errors == []
    month = repo.get_schedule_month(conn, YM)
    assert month.status == "CONFIRMED"
    assert month.confirmed_at


def test_confirm_requires_schedule(conn):
    _setup_month(conn)
    with pytest.raises(ValueError):
        services.confirm_month(conn, YM)


@pytest.mark.parametrize(
    "action",
    [
        lambda c, ids: services.generate_and_save(c, YM),
        lambda c, ids: services.apply_manual_edit(c, ids[0], DATES[0], True),
        lambda c, ids: services.apply_manual_edit(c, ids[0], DATES[0], True, is_locked=True),
        lambda c, ids: services.set_lock(c, ids[0], DATES[0], True),
        lambda c, ids: services.confirm_month(c, YM),
        # repository直接呼び出しも拒否（§48: UIだけでなくrepositoryでも）
        lambda c, ids: repo.update_assignment_manual(c, ids[0], DATES[0], True),
        lambda c, ids: repo.set_lock(c, ids[0], DATES[0], True),
    ],
    ids=["regenerate", "edit", "edit_lock", "lock", "reconfirm", "repo_edit", "repo_lock"],
)
def test_confirmed_month_rejects_changes(conn, action):
    ids = _setup_month(conn)
    services.generate_and_save(conn, YM)
    assert services.confirm_month(conn, YM).confirmed
    before = repo.load_assignments(conn, YM)
    before_month = repo.get_schedule_month(conn, YM)

    with pytest.raises(services.ScheduleConfirmedError):
        action(conn, ids)

    assert repo.load_assignments(conn, YM) == before
    assert repo.get_schedule_month(conn, YM) == before_month


def test_confirmed_month_does_not_block_other_months(conn):
    ids = _setup_month(conn)
    services.generate_and_save(conn, YM)
    services.confirm_month(conn, YM)
    march = get_month_dates("2026-03")
    for sid in ids:
        repo.save_monthly_condition(conn, MonthlyConditionInput(sid, "2026-03", 10 * 480))
    repo.save_daily_requirements(conn, [DailyRequirementInput(d, 1) for d in march])
    assert services.generate_and_save(conn, "2026-03").saved


def test_load_scheduler_input_includes_locks_and_inactive(conn):
    ids = _setup_month(conn)
    services.generate_and_save(conn, YM)
    services.set_lock(conn, ids[0], DATES[0], True)
    repo.deactivate_staff(conn, ids[2])
    inp = services.load_scheduler_input(conn, YM)
    assert [s.staff_id for s in inp.staff] == ids
    assert [s.active for s in inp.staff] == [True, True, False]
    assert [(l.staff_id, l.work_date) for l in inp.locked_assignments] == [(ids[0], DATES[0])]
    assert services.get_role_names(conn)[LEADER]


# ---------------------------------------------------------------------------
# v1.5 確定解除
# ---------------------------------------------------------------------------


def test_unconfirm_allows_re_edit_and_reconfirm(conn):
    ids = _setup_month(conn)
    services.generate_and_save(conn, YM)
    services.set_lock(conn, ids[0], DATES[0], True)
    assert services.confirm_month(conn, YM).confirmed
    before = repo.load_assignments(conn, YM)

    services.unconfirm_month(conn, YM)

    month = repo.get_schedule_month(conn, YM)
    assert month.status == "DRAFT"
    assert month.confirmed_at is None
    assert repo.load_assignments(conn, YM) == before  # 勤務・固定は維持
    assert _cell(conn, ids[0], DATES[0]).is_locked

    # 解除後は編集・再計算・再確定ができる
    services.apply_manual_edit(conn, ids[1], DATES[5], True, is_locked=True)
    assert services.generate_and_save(conn, YM).saved
    assert _cell(conn, ids[1], DATES[5]).is_working
    assert services.confirm_month(conn, YM).confirmed


def test_unconfirm_requires_confirmed_month(conn):
    _setup_month(conn)
    with pytest.raises(ValueError):
        services.unconfirm_month(conn, YM)  # 未生成
    services.generate_and_save(conn, YM)
    with pytest.raises(ValueError):
        services.unconfirm_month(conn, YM)  # DRAFT


def test_repository_unconfirm_errors(conn):
    _setup_month(conn)
    with pytest.raises(ValueError):
        repo.unconfirm_schedule_month(conn, YM)
    services.generate_and_save(conn, YM)
    with pytest.raises(ValueError):
        repo.unconfirm_schedule_month(conn, YM)
