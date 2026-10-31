"""代わりにできる役割のテスト.

10月のバックテストで、リーダー・チェッカーが1人しか出られない曜日は、その人が休むと
代わりがおらず必ず不足した。スタッフごとに「代わりにできる役割」を登録できるようにし、
勤務表づくり・保存済み勤務表の判定・体制チェックで、1人1役割として数える。
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import repositories as repo
from src import services
from src.coverage_check import analyze_coverage, describe_problems
from src.database import get_connection, initialize_database
from src.day_conditions import resolve_period_conditions
from src.models import (
    DailyRequirementInput,
    GenerationDay,
    GenerationRequest,
    GenerationStaff,
    RoleRequirementInput,
    StaffDetail,
    StaffInput,
)
from src.role_assignment import RolePerson, assign_roles, role_shortages
from src.shift_generation import generate_shift
from src.ui_common import DB_PATH_ENV

LEADER, CHECKER, CLEANER = 1, 2, 3
DAY = "2026-11-02"  # 月曜

# ---------------------------------------------------------------------------
# 1日の役割の割り当て
# ---------------------------------------------------------------------------


def test_own_role_first():
    people = [RolePerson(1, LEADER), RolePerson(2, CHECKER, (LEADER,))]
    assert assign_roles(people, {LEADER: 1, CHECKER: 1}) == {1: LEADER, 2: CHECKER}


def test_substitute_fills_the_missing_role():
    people = [RolePerson(1, CHECKER, (LEADER,)), RolePerson(2, CHECKER)]
    assert assign_roles(people, {LEADER: 1, CHECKER: 1}) == {1: LEADER, 2: CHECKER}
    assert role_shortages(people, {LEADER: 1, CHECKER: 1}) == {}


def test_one_person_cannot_fill_two_roles():
    people = [RolePerson(1, CHECKER, (LEADER,))]
    assert sum(role_shortages(people, {LEADER: 1, CHECKER: 1}).values()) == 1


def test_chain_of_substitutes():
    """クリーナー(チェッカー代行可)→チェッカー(リーダー代行可)の入れ替えで3役割を満たす."""
    people = [RolePerson(1, CLEANER, (CHECKER,)), RolePerson(2, CHECKER, (LEADER,)), RolePerson(3, CLEANER)]
    assert role_shortages(people, {LEADER: 1, CHECKER: 1, CLEANER: 1}) == {}


def test_without_substitutes_counts_are_unchanged():
    people = [RolePerson(1, CLEANER), RolePerson(2, CLEANER)]
    assert role_shortages(people, {LEADER: 1, CLEANER: 3}) == {LEADER: 1, CLEANER: 1}


# ---------------------------------------------------------------------------
# 勤務表づくり（Solver）
# ---------------------------------------------------------------------------


def gen_staff(staff_id, role_id, substitutes=()):
    detail = StaffDetail(
        staff=StaffInput(staff_id, f"{staff_id:04d}", f"S{staff_id}", role_id, 3, "清掃", True, "09:00", "15:30"),
        weekdays=tuple(range(7)),
    )
    conditions = {c.work_date: c for c in resolve_period_conditions(detail, [DAY], {})}
    return GenerationStaff(
        staff_id=staff_id, employee_code=f"{staff_id:04d}", staff_name=f"S{staff_id}",
        role_id=role_id, skill_level=3, day_conditions=conditions,
        substitute_role_ids=tuple(substitutes),
    )


def solve(staff, roles, total):
    return generate_shift(GenerationRequest(
        work_dates=[DAY], staff=staff,
        days=[GenerationDay(DAY, requirement_is_set=True, required_total_staff=total, role_requirements=roles)],
    ))


def test_solver_uses_a_substitute_when_no_leader_can_come():
    result = solve([gen_staff(1, CHECKER, (LEADER,)), gen_staff(2, CHECKER), gen_staff(3, CLEANER)],
                   {LEADER: 1, CHECKER: 1, CLEANER: 1}, 3)
    assert result.total_shortage == 0
    assert result.days[0].role_shortages == {}


def test_solver_without_substitutes_reports_the_gap():
    result = solve([gen_staff(1, CHECKER), gen_staff(2, CHECKER), gen_staff(3, CLEANER)],
                   {LEADER: 1, CHECKER: 1, CLEANER: 1}, 3)
    assert result.days[0].role_shortages == {LEADER: 1}


def test_solver_counts_one_role_per_person():
    result = solve([gen_staff(1, CHECKER, (LEADER,))], {LEADER: 1, CHECKER: 1}, 1)
    assert sum(result.days[0].role_shortages.values()) == 1


# ---------------------------------------------------------------------------
# 保存・保存済み勤務表の判定・体制チェック（DB）
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "substitute.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    connection = get_connection(path)
    initialize_database(connection)
    yield connection
    connection.close()


def add(conn, code, role_id, substitutes=None):
    return repo.create_staff(conn, code, f"S{code}", role_id, 3, standard_start_time="09:00",
                             standard_end_time="15:30", weekdays=list(range(7)),
                             substitute_role_ids=substitutes)


def test_substitutes_are_saved_without_the_own_role(conn):
    sid = add(conn, "1", CHECKER, [LEADER, CHECKER])
    assert repo.get_staff_detail(conn, sid).substitute_role_ids == (LEADER,)
    assert repo.list_staff_details(conn)[0].substitute_role_ids == (LEADER,)


def test_changing_the_own_role_drops_a_duplicate_substitute(conn):
    sid = add(conn, "1", CHECKER, [LEADER])
    staff = repo.get_staff(conn, sid)
    repo.update_staff(conn, sid, employee_code=staff.employee_code, staff_name=staff.staff_name,
                      role_id=LEADER, skill_level=3)
    assert repo.get_staff_detail(conn, sid).substitute_role_ids == ()


def test_update_replaces_substitutes(conn):
    sid = add(conn, "1", CLEANER, [CHECKER])
    staff = repo.get_staff(conn, sid)
    repo.update_staff(conn, sid, employee_code=staff.employee_code, staff_name=staff.staff_name,
                      role_id=CLEANER, skill_level=3, substitute_role_ids=[LEADER, CHECKER])
    assert repo.get_staff_detail(conn, sid).substitute_role_ids == (LEADER, CHECKER)


def save_requirement(conn, roles, total):
    assert not services.save_period_requirements(
        conn, [DAY], [DailyRequirementInput(DAY, total)],
        [RoleRequirementInput(DAY, r, n) for r, n in roles.items()],
    )


def test_generated_and_saved_schedule_has_no_role_shortage(conn):
    """代わりのリーダーで作った勤務表を保存しても、保存済みの判定でリーダー不足にならない."""
    add(conn, "1", CHECKER, [LEADER])
    add(conn, "2", CHECKER)
    add(conn, "3", CLEANER)
    save_requirement(conn, {LEADER: 1, CHECKER: 1, CLEANER: 1}, 3)
    request = services.build_regeneration_request(conn, [DAY])
    result = generate_shift(request)
    assert result.total_shortage == 0
    services.save_generated_schedule(conn, [DAY], result)
    day = services.validate_current_schedule(conn, [DAY]).days[0]
    assert not [i for i in day.issues if i.severity == "ERROR"]


def test_coverage_check_counts_substitutes(conn):
    add(conn, "1", CHECKER, [LEADER])
    add(conn, "2", CHECKER)
    add(conn, "3", CLEANER)
    save_requirement(conn, {LEADER: 1, CHECKER: 1, CLEANER: 1}, 3)
    assert not analyze_coverage(conn, [DAY])[0].has_problem


def test_coverage_check_explains_competition_between_roles(conn):
    add(conn, "1", CHECKER, [LEADER])
    add(conn, "3", CLEANER)
    save_requirement(conn, {LEADER: 1, CHECKER: 1}, 2)
    day = analyze_coverage(conn, [DAY])[0]
    text = " ".join(describe_problems(day))
    assert "1人足りません" in text and "ほかの役割でも必要です" in text


# ---------------------------------------------------------------------------
# スタッフ管理の画面
# ---------------------------------------------------------------------------

STAFF_PAGE = str(Path(__file__).resolve().parents[1] / "app_pages" / "01_staff.py")


def test_staff_page_saves_substitute_roles(conn):
    at = AppTest.from_file(STAFF_PAGE, default_timeout=30).run()
    at.text_input[0].input("0050")
    at.text_input[1].input("代行さん")
    at.multiselect(key="create_substitute_roles").set_value(["チェッカー"])
    [b for b in at.button if b.label == "登録"][0].click()
    at.run()
    assert not at.exception
    created = repo.get_staff_by_employee_code(conn, "0050")
    assert repo.get_staff_detail(conn, created.staff_id).substitute_role_ids == (CHECKER,)
    table = at.dataframe[0].value
    assert table[table["従業員番号"] == "0050"].iloc[0]["代わりにできる役割"] == "チェッカー"


def test_staff_page_edit_shows_saved_substitutes(conn):
    sid = add(conn, "1", CLEANER, [CHECKER])
    at = AppTest.from_file(STAFF_PAGE, default_timeout=30).run()
    assert at.multiselect(key=f"edit_{sid}_substitute_roles").value == [CHECKER]
