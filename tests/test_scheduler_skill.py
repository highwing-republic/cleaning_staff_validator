"""SK10: skill_level追加によるシフト最適化への回帰テスト（TEST09, §25-§28, §33）.

スキルはSolverの制約・目的関数に一切使用しない。skill_levelの値を変えても
Hard Constraint挙動・目的値・割当結果は変わらないこと、及びscheduler.py /
precheck.py / schedule_validation.py に"skill"という文字列が一切現れないことを確認する。
"""

from pathlib import Path

import dataclasses

from scheduler_helpers import assert_hard_constraints, cond, make_input, staff
from src.scheduler import generate_schedule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _with_skill(staff_list, skill_level: int):
    return [dataclasses.replace(s, skill_level=skill_level) for s in staff_list]


def test_all_skill_1_vs_all_skill_5_identical_result():
    base_staff = [staff(1), staff(2), staff(3)]
    conditions = [cond(i, 20) for i in (1, 2, 3)]

    inp_low = make_input(_with_skill(base_staff, 1), conditions=conditions)
    inp_high = make_input(_with_skill(base_staff, 5), conditions=conditions)

    result_low = generate_schedule(inp_low)
    result_high = generate_schedule(inp_high)

    assert_hard_constraints(inp_low, result_low)
    assert_hard_constraints(inp_high, result_high)

    assert result_low.status == result_high.status
    assert result_low.objective_overstaff == result_high.objective_overstaff
    assert result_low.objective_target_deviation == result_high.objective_target_deviation
    assert result_low.objective_prefer_off == result_high.objective_prefer_off
    assert result_low.objective_prefer_work == result_high.objective_prefer_work
    assert set(result_low.assignments) == set(result_high.assignments)


def test_mixed_skill_levels_identical_result_to_uniform():
    base_staff = [staff(1), staff(2), staff(3)]
    conditions = [cond(i, 20) for i in (1, 2, 3)]

    inp_uniform = make_input(_with_skill(base_staff, 3), conditions=conditions)
    mixed_staff = [
        dataclasses.replace(base_staff[0], skill_level=1),
        dataclasses.replace(base_staff[1], skill_level=3),
        dataclasses.replace(base_staff[2], skill_level=5),
    ]
    inp_mixed = make_input(mixed_staff, conditions=conditions)

    result_uniform = generate_schedule(inp_uniform)
    result_mixed = generate_schedule(inp_mixed)

    assert_hard_constraints(inp_uniform, result_uniform)
    assert_hard_constraints(inp_mixed, result_mixed)

    assert result_uniform.status == result_mixed.status
    assert result_uniform.objective_overstaff == result_mixed.objective_overstaff
    assert result_uniform.objective_target_deviation == result_mixed.objective_target_deviation
    assert result_uniform.objective_prefer_off == result_mixed.objective_prefer_off
    assert result_uniform.objective_prefer_work == result_mixed.objective_prefer_work
    assert set(result_uniform.assignments) == set(result_mixed.assignments)


def test_scheduler_modules_do_not_reference_skill():
    """scheduler.py / precheck.py / schedule_validation.py に"skill"が出現しないこと（§25-§28, §33）."""
    for relative in ("src/scheduler.py", "src/precheck.py", "src/schedule_validation.py"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "skill" not in text.lower(), f"{relative} references skill"
