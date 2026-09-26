"""日別清掃体制検証エンジン（DB非依存）のテスト."""

import pytest

from src import staffing_validation as sv
from src.models import (
    AttendanceShiftInput,
    DailyRequirementInput,
    RoleRequirementInput,
    StaffInput,
    ValidationIssue,
)
from src.staffing_validation import validate_day, validate_month_staffing, worst_status

D = "2026-09-01"
LEADER, CHECKER, CLEANER = 1, 2, 3
ROLE_NAMES = {LEADER: "リーダー", CHECKER: "チェッカー", CLEANER: "クリーナー"}

_counter = iter(range(1, 10_000))


def _code():
    return f"{next(_counter):04d}"


def staff(staff_id, role_id=CLEANER, skill=3, active=True):
    return StaffInput(staff_id, f"{staff_id:04d}", f"S{staff_id}", role_id, skill, "清掃", active)


def cleaning(staff_id=None, work_date=D):
    """清掃 + TIME_RANGE（確定清掃勤務）."""
    return AttendanceShiftInput(
        employee_code=_code(), work_date=work_date, raw_shift="09:00-15:30",
        shift_type="TIME_RANGE", available_for_cleaning=True, staff_id=staff_id,
        department="清掃", start_minutes=540, end_minutes=930,
    )


def unknown(staff_id=None, department="清掃", work_date=D):
    return AttendanceShiftInput(
        employee_code=_code(), work_date=work_date, raw_shift="特別勤務A",
        shift_type="UNKNOWN", available_for_cleaning=False, staff_id=staff_id,
        department=department,
    )


def other_duty(staff_id=None, work_date=D):
    return AttendanceShiftInput(
        employee_code=_code(), work_date=work_date, raw_shift="深夜フロント（夜勤）",
        shift_type="OTHER_DUTY", available_for_cleaning=False, staff_id=staff_id,
        department="清掃",
    )


def blank(staff_id=None, work_date=D):
    return AttendanceShiftInput(
        employee_code=_code(), work_date=work_date, raw_shift="", shift_type="BLANK",
        available_for_cleaning=False, staff_id=staff_id, department="清掃",
    )


def morning(staff_id=None, work_date=D):
    """清掃以外の部門の TIME_RANGE."""
    return AttendanceShiftInput(
        employee_code=_code(), work_date=work_date, raw_shift="05:30-11:30",
        shift_type="TIME_RANGE", available_for_cleaning=False, staff_id=staff_id,
        department="朝", start_minutes=330, end_minutes=690,
    )


def req(required=0, max_total=None, level=None, count=0, work_date=D):
    return DailyRequirementInput(
        work_date=work_date, required_total_staff=required, max_total_staff=max_total,
        required_skill_level=level, required_skill_count=count,
    )


def run(shifts, requirement, roles=None, staff_list=()):
    return validate_day(
        D, shifts, requirement, roles or {}, {s.staff_id: s for s in staff_list}, ROLE_NAMES
    )


def codes(result):
    return [i.code for i in result.issues]


def issue(result, code):
    return next(i for i in result.issues if i.code == code)


# ---------------------------------------------------------------------------
# status 優先順位
# ---------------------------------------------------------------------------


def test_worst_status_priority():
    w = ValidationIssue("A", "WARNING", "m", D)
    e = ValidationIssue("B", "ERROR", "m", D)
    assert worst_status([]) == "OK"
    assert worst_status([w]) == "WARNING"
    assert worst_status([w, e, w]) == "ERROR"
    assert worst_status([e, w]) == "ERROR"


def test_invalid_severity_rejected():
    with pytest.raises(ValueError):
        ValidationIssue("A", "INFO", "m", D)


# ---------------------------------------------------------------------------
# 人数（最低）
# ---------------------------------------------------------------------------


def test_staff_required8_known8_ok():
    staff_list = [staff(i) for i in range(1, 9)]
    result = run([cleaning(s.staff_id) for s in staff_list], req(8), staff_list=staff_list)
    assert result.actual_cleaning_staff == 8
    assert result.status == "OK"
    assert result.issues == []


def test_staff_required8_known7_unknown1_warning():
    staff_list = [staff(i) for i in range(1, 9)]
    shifts = [cleaning(i) for i in range(1, 8)] + [unknown(8)]
    result = run(shifts, req(8), staff_list=staff_list)
    assert result.actual_cleaning_staff == 7
    assert result.unknown_cleaning_shift_count == 1
    assert result.status == "WARNING"
    assert codes(result) == [sv.STAFF_UNCERTAIN, sv.UNKNOWN_SHIFT]
    assert sv.STAFF_SHORTAGE not in codes(result)


def test_staff_required8_known6_unknown1_error():
    staff_list = [staff(i) for i in range(1, 8)]
    shifts = [cleaning(i) for i in range(1, 7)] + [unknown(7)]
    result = run(shifts, req(8), staff_list=staff_list)
    assert result.status == "ERROR"
    shortage = issue(result, sv.STAFF_SHORTAGE)
    assert shortage.severity == "ERROR"
    assert (shortage.required, shortage.actual, shortage.possible) == (8, 6, 7)
    assert "1名以上不足" in shortage.message
    assert "必要：8名" in shortage.message and "確定清掃勤務：6名" in shortage.message
    assert "勤務区分不明：1名" in shortage.message and "最大でも7名" in shortage.message
    # UNKNOWN の WARNING も残るが status は ERROR
    assert sv.UNKNOWN_SHIFT in codes(result)


def test_explicit_zero_required_is_ok_with_workers():
    staff_list = [staff(i) for i in range(1, 4)]
    result = run([cleaning(i) for i in range(1, 4)], req(0), staff_list=staff_list)
    assert result.requirement_defined is True
    assert result.required_staff == 0
    assert result.actual_cleaning_staff == 3
    assert result.status == "OK"


# ---------------------------------------------------------------------------
# 上限
# ---------------------------------------------------------------------------


def test_max8_known9_error():
    staff_list = [staff(i) for i in range(1, 10)]
    result = run([cleaning(i) for i in range(1, 10)], req(0, max_total=8), staff_list=staff_list)
    assert result.status == "ERROR"
    over = issue(result, sv.STAFF_OVER_MAX)
    assert (over.required, over.actual) == (8, 9)


def test_max8_known8_unknown1_warning():
    staff_list = [staff(i) for i in range(1, 10)]
    shifts = [cleaning(i) for i in range(1, 9)] + [unknown(9)]
    result = run(shifts, req(0, max_total=8), staff_list=staff_list)
    assert result.status == "WARNING"
    assert codes(result) == [sv.STAFF_OVER_MAX_UNCERTAIN, sv.UNKNOWN_SHIFT]


def test_max8_known7_unknown1_only_unknown_warning():
    staff_list = [staff(i) for i in range(1, 9)]
    shifts = [cleaning(i) for i in range(1, 8)] + [unknown(8)]
    result = run(shifts, req(0, max_total=8), staff_list=staff_list)
    # 上限条件そのものは違反の可能性なし。UNKNOWN_SHIFT の WARNING のみ
    assert codes(result) == [sv.UNKNOWN_SHIFT]
    assert result.status == "WARNING"


def test_max_none_not_checked():
    staff_list = [staff(i) for i in range(1, 21)]
    result = run([cleaning(i) for i in range(1, 21)], req(1), staff_list=staff_list)
    assert result.status == "OK"


# ---------------------------------------------------------------------------
# ロール
# ---------------------------------------------------------------------------


def test_role_known_leader_ok():
    staff_list = [staff(1, LEADER), staff(2, CLEANER)]
    result = run([cleaning(1), cleaning(2)], req(2), {LEADER: 1}, staff_list)
    assert result.actual_roles[LEADER] == 1
    assert result.required_roles[LEADER] == 1
    assert result.status == "OK"


def test_role_unmatched_cleaning_worker_makes_warning():
    staff_list = [staff(2, CLEANER)]
    result = run([cleaning(2), cleaning(None)], req(2), {LEADER: 1}, staff_list)
    assert result.actual_roles[LEADER] == 0
    assert result.possible_roles[LEADER] == 1
    assert result.status == "WARNING"
    assert codes(result) == [sv.ROLE_UNCERTAIN, sv.UNMATCHED_STAFF]
    uncertain = issue(result, sv.ROLE_UNCERTAIN)
    assert uncertain.role_id == LEADER
    assert "リーダー" in uncertain.message


def test_role_no_potential_error():
    staff_list = [staff(1, CLEANER), staff(2, CHECKER)]
    result = run([cleaning(1), cleaning(2)], req(2), {LEADER: 1}, staff_list)
    assert result.status == "ERROR"
    shortage = issue(result, sv.ROLE_SHORTAGE)
    assert (shortage.required, shortage.actual, shortage.possible) == (1, 0, 0)
    assert shortage.role_id == LEADER


def test_role_potential_includes_matched_unknown_shift_of_that_role():
    staff_list = [staff(1, LEADER), staff(2, CLEANER)]
    result = run([unknown(1), cleaning(2)], req(1), {LEADER: 1}, staff_list)
    assert (result.actual_roles[LEADER], result.possible_roles[LEADER]) == (0, 1)
    assert sv.ROLE_UNCERTAIN in codes(result)


def test_role_potential_excludes_unknown_shift_of_other_role():
    staff_list = [staff(1, CHECKER), staff(2, CLEANER)]
    result = run([unknown(1), cleaning(2)], req(1), {LEADER: 1}, staff_list)
    assert result.possible_roles[LEADER] == 0
    assert sv.ROLE_SHORTAGE in codes(result)


def test_role_potential_includes_unmatched_unknown_shift():
    result = run([unknown(None)], req(0), {LEADER: 1})
    assert result.possible_roles[LEADER] == 1
    assert sv.ROLE_UNCERTAIN in codes(result)


def test_missing_role_row_means_zero_required():
    staff_list = [staff(1, CLEANER)]
    result = run([cleaning(1)], req(1), {}, staff_list)
    assert result.required_roles == {LEADER: 0, CHECKER: 0, CLEANER: 0}
    assert result.status == "OK"


def test_role_sum_exceeding_minimum_is_not_an_error():
    staff_list = [staff(i, LEADER) for i in range(1, 4)] + [staff(i, CHECKER) for i in range(4, 7)]
    result = run(
        [cleaning(i) for i in range(1, 7)], req(5), {LEADER: 3, CHECKER: 3}, staff_list
    )
    assert result.status == "OK"


def test_inactive_staff_counts_with_master_role():
    staff_list = [staff(1, LEADER, active=False)]
    result = run([cleaning(1)], req(1), {LEADER: 1}, staff_list)
    assert result.actual_cleaning_staff == 1
    assert result.actual_roles[LEADER] == 1
    assert result.status == "OK"


# ---------------------------------------------------------------------------
# 複合ロール（1人1ロール制約）
# ---------------------------------------------------------------------------


def test_rc01_one_generic_candidate_cannot_fill_two_roles():
    result = run([cleaning(None)], req(1), {LEADER: 1, CHECKER: 1})
    # 個別には両ロールとも充足可能に見える
    assert result.possible_roles[LEADER] == 1 and result.possible_roles[CHECKER] == 1
    assert sv.ROLE_SHORTAGE not in codes(result)
    assert result.status == "ERROR"
    combo = issue(result, sv.ROLE_COMBINATION_SHORTAGE)
    assert combo.severity == "ERROR"
    assert (combo.required, combo.possible) == (2, 1)
    assert "ロール不足合計：2名" in combo.message
    assert "割当可能な未登録候補：1名" in combo.message


def test_rc02_two_generic_candidates_warning():
    result = run([cleaning(None), cleaning(None)], req(2), {LEADER: 1, CHECKER: 1})
    assert sv.ROLE_COMBINATION_SHORTAGE not in codes(result)
    assert result.status == "WARNING"
    assert codes(result).count(sv.ROLE_UNCERTAIN) == 2


def test_rc03_fixed_role_unknown_plus_generic_warning():
    staff_list = [staff(1, LEADER)]
    result = run([unknown(1), cleaning(None)], req(1), {LEADER: 1, CHECKER: 1}, staff_list)
    assert sv.ROLE_COMBINATION_SHORTAGE not in codes(result)
    assert result.status == "WARNING"


def test_rc04_known_roles_ok():
    staff_list = [staff(1, LEADER), staff(2, CHECKER)]
    result = run([cleaning(1), cleaning(2)], req(2), {LEADER: 1, CHECKER: 1}, staff_list)
    assert result.issues == []
    assert result.status == "OK"


def test_rc05_partial_known_remaining_two_candidate_one_error():
    staff_list = [staff(1, LEADER), staff(2, CHECKER)]
    shifts = [cleaning(1), cleaning(2), cleaning(None)]
    result = run(shifts, req(3), {LEADER: 2, CHECKER: 2}, staff_list)
    assert sv.ROLE_SHORTAGE not in codes(result)
    combo = issue(result, sv.ROLE_COMBINATION_SHORTAGE)
    assert (combo.required, combo.possible) == (2, 1)
    assert result.status == "ERROR"


def test_combination_fixed_unknown_of_other_role_does_not_help():
    # CHECKERのUNKNOWN登録者はLEADER不足を埋められない
    staff_list = [staff(1, CHECKER)]
    result = run([unknown(1), cleaning(None)], req(1), {LEADER: 2}, staff_list)
    assert result.possible_roles[LEADER] == 1
    assert sv.ROLE_SHORTAGE in codes(result)  # 個別判定で既に不足
    assert sv.ROLE_COMBINATION_SHORTAGE not in codes(result)  # 重複して出さない
    assert result.status == "ERROR"


def test_combination_includes_cleaner_role_and_unknown_generic():
    # 未登録のUNKNOWN勤務者1名も候補だが、リーダーとクリーナーを同時には満たせない
    result = run([unknown(None)], req(0), {LEADER: 1, CLEANER: 1})
    assert sv.ROLE_COMBINATION_SHORTAGE in codes(result)
    assert result.status == "ERROR"


def test_combination_fixed_unknowns_fill_their_own_roles():
    staff_list = [staff(1, LEADER), staff(2, CHECKER)]
    result = run([unknown(1), unknown(2)], req(0), {LEADER: 1, CHECKER: 1}, staff_list)
    assert sv.ROLE_COMBINATION_SHORTAGE not in codes(result)
    assert result.status == "WARNING"


def test_combination_not_checked_when_requirement_missing():
    result = run([cleaning(None)], None, {LEADER: 1, CHECKER: 1})
    assert codes(result) == [sv.REQUIREMENT_MISSING, sv.UNMATCHED_STAFF]


# ---------------------------------------------------------------------------
# スキル
# ---------------------------------------------------------------------------


def test_skill_known2_ok():
    staff_list = [staff(1, skill=4), staff(2, skill=5), staff(3, skill=3)]
    result = run([cleaning(i) for i in (1, 2, 3)], req(3, level=4, count=2), staff_list=staff_list)
    assert (result.actual_skill_count, result.possible_skill_count) == (2, 2)
    assert result.status == "OK"


def test_skill_known1_unmatched1_warning():
    staff_list = [staff(1, skill=4), staff(2, skill=2)]
    shifts = [cleaning(1), cleaning(2), cleaning(None)]
    result = run(shifts, req(3, level=4, count=2), staff_list=staff_list)
    assert (result.actual_skill_count, result.possible_skill_count) == (1, 2)
    assert result.status == "WARNING"
    assert codes(result) == [sv.SKILL_UNCERTAIN, sv.UNMATCHED_STAFF]
    assert "スキル4以上" in issue(result, sv.SKILL_UNCERTAIN).message


def test_skill_potential_insufficient_error():
    staff_list = [staff(1, skill=4), staff(2, skill=3)]
    result = run([cleaning(1), cleaning(2)], req(2, level=4, count=2), staff_list=staff_list)
    assert (result.actual_skill_count, result.possible_skill_count) == (1, 1)
    assert result.status == "ERROR"
    shortage = issue(result, sv.SKILL_SHORTAGE)
    assert (shortage.required, shortage.actual, shortage.possible) == (2, 1, 1)


def test_skill_potential_includes_matched_unknown_shift_with_skill():
    staff_list = [staff(1, skill=4), staff(2, skill=5), staff(3, skill=2)]
    shifts = [cleaning(1), unknown(2), unknown(3)]
    result = run(shifts, req(1, level=4, count=2), staff_list=staff_list)
    # 低スキル(3番)のUNKNOWNは potential に入らない
    assert (result.actual_skill_count, result.possible_skill_count) == (1, 2)
    assert sv.SKILL_UNCERTAIN in codes(result)


def test_skill_condition_zero_count_not_checked():
    staff_list = [staff(1, skill=1)]
    result = run([cleaning(1)], req(1, level=5, count=0), staff_list=staff_list)
    assert result.actual_skill_count is None
    assert result.status == "OK"


# ---------------------------------------------------------------------------
# 要件未設定
# ---------------------------------------------------------------------------


def test_requirement_missing_is_warning_not_zero_required():
    staff_list = [staff(1, LEADER)]
    result = run([cleaning(1), cleaning(None)], None, {LEADER: 5}, staff_list)
    assert result.requirement_defined is False
    assert result.required_staff is None
    assert result.status == "WARNING"
    assert codes(result) == [sv.REQUIREMENT_MISSING, sv.UNMATCHED_STAFF]
    # 集計は表示できる
    assert result.actual_cleaning_staff == 2
    assert result.actual_roles[LEADER] == 1
    assert result.unmatched_working_count == 1


def test_requirement_missing_with_no_workers_is_still_warning():
    result = run([], None)
    assert result.status == "WARNING"
    assert codes(result) == [sv.REQUIREMENT_MISSING]


# ---------------------------------------------------------------------------
# 未登録 / UNKNOWN / OTHER_DUTY
# ---------------------------------------------------------------------------


def test_unmatched_cleaning_counted_in_total_not_in_known_role_or_skill():
    staff_list = [staff(1, LEADER, skill=5)]
    shifts = [cleaning(1), cleaning(None)]
    result = run(shifts, req(2, level=4, count=1), {LEADER: 1}, staff_list)
    assert result.actual_cleaning_staff == 2
    assert result.unmatched_working_count == 1
    assert result.actual_roles == {LEADER: 1, CHECKER: 0, CLEANER: 0}
    assert result.actual_skill_count == 1
    assert result.status == "WARNING"
    assert codes(result) == [sv.UNMATCHED_STAFF]


def test_unknown_cleaning_not_actual_but_potential():
    result = run([unknown(None)], req(1))
    assert result.actual_cleaning_staff == 0
    assert result.unknown_cleaning_shift_count == 1
    assert result.status == "WARNING"
    assert codes(result) == [sv.STAFF_UNCERTAIN, sv.UNKNOWN_SHIFT, sv.UNMATCHED_STAFF]
    # 未登録UNKNOWNは unmatched_working_count（確定清掃勤務の未登録数）には入らない
    assert result.unmatched_working_count == 0


def test_unknown_in_other_department_ignored():
    result = run([unknown(None, department="朝")], req(0))
    assert result.unknown_cleaning_shift_count == 0
    assert result.status == "OK"


def test_other_duty_excluded_from_actual_and_potential():
    staff_list = [staff(1, LEADER, skill=5)]
    result = run([other_duty(1), other_duty(None)], req(1, level=4, count=1), {LEADER: 1}, staff_list)
    assert result.actual_cleaning_staff == 0
    assert result.unknown_cleaning_shift_count == 0
    assert result.possible_roles[LEADER] == 0
    assert result.possible_skill_count == 0
    assert set(codes(result)) == {sv.STAFF_SHORTAGE, sv.ROLE_SHORTAGE, sv.SKILL_SHORTAGE}
    assert sv.UNMATCHED_STAFF not in codes(result)


def test_blank_and_other_department_ignored():
    result = run([blank(1), morning(2), morning(None)], req(0), staff_list=[staff(1), staff(2)])
    assert result.actual_cleaning_staff == 0
    assert result.issues == []


# ---------------------------------------------------------------------------
# 月間
# ---------------------------------------------------------------------------


def test_validate_month_covers_all_dates_and_uses_per_date_requirements():
    staff_list = [staff(1, LEADER)]
    shifts = [cleaning(1, work_date="2026-09-01"), cleaning(1, work_date="2026-09-02")]
    reqs = [req(1, work_date="2026-09-01"), req(2, work_date="2026-09-02")]
    roles = [RoleRequirementInput("2026-09-02", LEADER, 1)]
    days = validate_month_staffing("2026-09", shifts, reqs, roles, staff_list, ROLE_NAMES)

    assert [d.work_date for d in days][:2] == ["2026-09-01", "2026-09-02"]
    assert len(days) == 30
    assert days[0].status == "OK"
    assert days[0].required_roles[LEADER] == 0
    assert days[1].status == "ERROR"
    assert days[1].required_roles[LEADER] == 1
    assert codes(days[1]) == [sv.STAFF_SHORTAGE]
    # 要件行のない日は要件未設定
    assert all(not d.requirement_defined for d in days[2:])
    assert all(d.status == "WARNING" for d in days[2:])
