"""予約・必要人数の「かんたん入力」のテスト.

1日に10個の入力欄が並ぶと最初の入力がつらいので、ふだんは 予約室数（任意）・必要人数・備考 だけを入れ、
役割の内訳は必要人数から自動で決める（リーダー1・チェッカー1・残りクリーナー）。
詳しい設定で内訳を個別に変えた日は、その内訳を守る。
"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import guided_workflow as workflow
from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database
from src.models import DailyRequirementInput, DailyRequirementView, RoleRequirementInput
from src.period_utils import period_dates
from src.requirement_defaults import (
    auto_role_counts,
    default_role_counts,
    describe_role_counts,
    roles_follow_total,
)
from src.simple_requirements import SimpleEntry, build_simple_save, follows_total, roles_for_total
from src.ui_common import DB_PATH_ENV

LEADER, CHECKER, CLEANER = 1, 2, 3
ROLES = [
    {"role_id": LEADER, "role_code": "LEADER", "role_name": "リーダー"},
    {"role_id": CHECKER, "role_code": "CHECKER", "role_name": "チェッカー"},
    {"role_id": CLEANER, "role_code": "CLEANER", "role_name": "クリーナー"},
]
MONDAY, SATURDAY = "2026-11-16", "2026-11-14"


# ---------------------------------------------------------------------------
# 必要人数から役割の内訳を決める
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (7, {LEADER: 1, CHECKER: 1, CLEANER: 5}),
        (6, {LEADER: 1, CHECKER: 1, CLEANER: 4}),
        (2, {LEADER: 1, CHECKER: 1, CLEANER: 0}),
        (1, {LEADER: 1, CHECKER: 0, CLEANER: 0}),
        (0, {LEADER: 0, CHECKER: 0, CLEANER: 0}),
    ],
)
def test_auto_role_counts(total, expected):
    assert auto_role_counts(total, ROLES) == expected


def test_default_breakdown_matches_the_automatic_rule():
    """初期値（平日7名・土日6名）の内訳は、自動の決め方と同じ."""
    assert default_role_counts(MONDAY, ROLES) == auto_role_counts(7, ROLES)
    assert default_role_counts(SATURDAY, ROLES) == auto_role_counts(6, ROLES)


def test_other_roles_get_no_automatic_count():
    roles = ROLES + [{"role_id": 9, "role_code": "INSPECTOR", "role_name": "点検"}]
    assert auto_role_counts(5, roles)[9] == 0


def test_roles_follow_total():
    assert roles_follow_total({}, False, None, ROLES)  # 役割の行がない
    assert roles_follow_total({}, True, 7, ROLES)
    assert roles_follow_total({LEADER: 1, CHECKER: 1, CLEANER: 5}, True, 7, ROLES)
    assert roles_follow_total({LEADER: 1, CHECKER: 1, CLEANER: 5, 9: 0}, True, 7, ROLES)  # 0の行は気にしない
    assert not roles_follow_total({LEADER: 1, CHECKER: 2, CLEANER: 4}, True, 7, ROLES)  # 個別
    assert not roles_follow_total({LEADER: 1, CHECKER: 1, CLEANER: 5}, True, 8, ROLES)  # 人数と合わない
    assert not roles_follow_total({LEADER: 0, CHECKER: 0, CLEANER: 0}, True, 7, ROLES)  # 0人と明示


def test_describe_role_counts():
    assert describe_role_counts({LEADER: 1, CHECKER: 2, CLEANER: 0}, ROLES) == "リーダー1・チェッカー2"
    assert describe_role_counts({}, ROLES) == "指定なし"


# ---------------------------------------------------------------------------
# かんたん入力の保存内容
# ---------------------------------------------------------------------------


def view(work_date, total=None, roles=None, **fields):
    requirement = None if total is None else DailyRequirementInput(work_date, total, **fields)
    return DailyRequirementView(work_date, requirement, dict(roles or {}))


def role_map(role_requirements, work_date):
    return {r.role_id: r.required_count for r in role_requirements if r.work_date == work_date}


def test_unsaved_day_gets_breakdown_from_the_count():
    requirements, roles = build_simple_save([view(MONDAY)], {MONDAY: SimpleEntry(8, 12, " 団体 ")}, ROLES)
    assert requirements == [DailyRequirementInput(MONDAY, 8, note="団体", reserved_rooms=12)]
    assert role_map(roles, MONDAY) == {LEADER: 1, CHECKER: 1, CLEANER: 6}


def test_changing_the_count_changes_an_automatic_breakdown():
    saved = view(MONDAY, 7, {LEADER: 1, CHECKER: 1, CLEANER: 5})
    _, roles = build_simple_save([saved], {MONDAY: SimpleEntry(5)}, ROLES)
    assert role_map(roles, MONDAY) == {LEADER: 1, CHECKER: 1, CLEANER: 3}


def test_customized_breakdown_is_kept():
    saved = view(MONDAY, 7, {LEADER: 1, CHECKER: 2, CLEANER: 4})
    _, roles = build_simple_save([saved], {MONDAY: SimpleEntry(9)}, ROLES)
    assert role_map(roles, MONDAY) == {LEADER: 1, CHECKER: 2, CLEANER: 4}
    assert not follows_total(saved, ROLES)


def test_fields_not_on_the_simple_form_are_kept():
    saved = view(MONDAY, 7, {}, max_total_staff=9, occupancy_rate=0.8,
                 required_skill_level=3, required_skill_count=2, reserved_rooms=20, note="旧")
    requirements, _ = build_simple_save([saved], {MONDAY: SimpleEntry(6, None, "")}, ROLES)
    assert requirements == [DailyRequirementInput(
        MONDAY, 6, max_total_staff=9, occupancy_rate=0.8, note=None,
        required_skill_level=3, required_skill_count=2, reserved_rooms=None,
    )]


def test_roles_for_total_keeps_roles_of_an_unsaved_day_with_role_rows():
    """要件の行がないのに役割の行だけある日（取り込みなど）は、その内訳を残す（以前と同じ）."""
    odd = view(MONDAY, None, {CLEANER: 2})
    assert roles_for_total(odd, 7, ROLES) == [RoleRequirementInput(MONDAY, CLEANER, 2)]


# ---------------------------------------------------------------------------
# 画面（AppTest）
# ---------------------------------------------------------------------------

PAGE = str(Path(__file__).resolve().parents[1] / "pages" / "03_requirements.py")
START = "2026-11-14"
DATES = period_dates(START, 14)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "simple.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    conn = get_connection(str(path))
    initialize_database(conn)
    conn.close()
    return path


def open_page():
    at = AppTest.from_file(PAGE, default_timeout=120)
    at.session_state["requirement_period_start"] = date.fromisoformat(START)
    at.session_state["requirement_period_days"] = 14
    at.run()
    assert not at.exception
    return at


def saved_views(db_path):
    conn = get_connection(str(db_path))
    views = services.get_period_requirements(conn, DATES)
    conn.close()
    return {v.work_date: v for v in views}


def press_save(at):
    next(b for b in at.button if b.label == "保存する").click()
    at.run()
    assert not at.exception


def test_simple_form_is_the_default_and_shows_only_three_inputs_per_day(db_path):
    at = open_page()
    assert at.toggle(key="requirements_advanced").value is False
    keys = {w.key for w in list(at.number_input) + list(at.text_input) + list(at.checkbox)}
    day_keys = {k for k in keys if k and k.startswith("req_")}
    assert len(day_keys) == 3 * len(DATES)
    assert f"req_simple_{DATES[0]}_required" in day_keys
    assert not any(k.startswith(f"req_{DATES[0]}_") for k in day_keys)  # 詳しい入力欄は出ない
    assert any("まだ保存していない日が14日" in i.value for i in at.info)


def test_simple_form_initial_counts_are_weekday_7_and_weekend_6(db_path):
    at = open_page()
    assert at.number_input(key=f"req_simple_{SATURDAY}_required").value == 6
    assert at.number_input(key=f"req_simple_{MONDAY}_required").value == 7


def test_simple_save_saves_all_days_with_automatic_breakdown(db_path):
    at = open_page()
    at.number_input(key=f"req_simple_{MONDAY}_required").set_value(9)
    at.number_input(key=f"req_simple_{MONDAY}_reserved").set_value(30)
    at.text_input(key=f"req_simple_{MONDAY}_note").input("団体あり")
    press_save(at)

    views = saved_views(db_path)
    assert all(v.is_defined for v in views.values())
    monday = views[MONDAY]
    assert monday.requirement.required_total_staff == 9
    assert monday.requirement.reserved_rooms == 30
    assert monday.requirement.note == "団体あり"
    assert monday.role_counts == {LEADER: 1, CHECKER: 1, CLEANER: 7}
    assert views[SATURDAY].role_counts == {LEADER: 1, CHECKER: 1, CLEANER: 4}
    assert any("保存しました（14日分）" in s.value for s in at.success)


def test_simple_save_keeps_a_customized_day_and_shows_it(db_path):
    conn = get_connection(str(db_path))
    assert not services.save_period_requirements(
        conn, [MONDAY], [DailyRequirementInput(MONDAY, 7, max_total_staff=8)],
        [RoleRequirementInput(MONDAY, LEADER, 1), RoleRequirementInput(MONDAY, CHECKER, 2),
         RoleRequirementInput(MONDAY, CLEANER, 4)],
    )
    conn.close()
    at = open_page()
    assert any("個別に設定（リーダー1・チェッカー2・クリーナー4）・最大8名" in c.value for c in at.caption)
    at.number_input(key=f"req_simple_{MONDAY}_required").set_value(8)
    press_save(at)

    monday = saved_views(db_path)[MONDAY]
    assert monday.requirement.required_total_staff == 8
    assert monday.requirement.max_total_staff == 8
    assert monday.role_counts == {LEADER: 1, CHECKER: 2, CLEANER: 4}


def test_advanced_toggle_shows_the_detailed_form(db_path):
    at = open_page()
    at.toggle(key="requirements_advanced").set_value(True).run()
    assert not at.exception
    assert at.checkbox(key=f"req_{MONDAY}_defined").value is True
    assert at.number_input(key=f"req_{MONDAY}_role_{CLEANER}").value == 5
    assert any(b.label == "この期間の予約・必要人数を保存" for b in at.button)


# ---------------------------------------------------------------------------
# かんたん作成の②（人数の保存）
# ---------------------------------------------------------------------------


def test_guided_count_change_moves_an_automatic_breakdown():
    conn = get_connection(":memory:")
    initialize_database(conn)
    workflow.initialize(conn)
    dates = [MONDAY]
    assert not services.save_period_requirements(
        conn, dates, [DailyRequirementInput(MONDAY, 7)],
        [RoleRequirementInput(MONDAY, r, n) for r, n in ((LEADER, 1), (CHECKER, 1), (CLEANER, 5))],
    )
    workflow.save_staff_counts(conn, dates, {MONDAY: 4}, workflow.review_fingerprint(conn, dates, "requirements"))
    counts = {r.role_id: r.required_count for r in repo.list_role_requirements(conn, MONDAY, MONDAY)}
    assert counts == {LEADER: 1, CHECKER: 1, CLEANER: 2}
    conn.close()
