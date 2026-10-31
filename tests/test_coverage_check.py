"""体制チェック（作る前に必ず足りなくなる日と理由）・休み希望の警告・設定の食い違いのテスト."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import repositories as repo
from src import services
from src.coverage_check import (
    absence_warning,
    analyze_coverage,
    describe_problems,
    explain_shortage,
    load_coverage_input,
    max_days_per_week,
    problem_days,
    staff_setting_warnings,
)
from src.database import get_connection, initialize_database
from src.models import (
    DailyGenerationResult,
    DailyRequirementInput,
    RoleRequirementInput,
    StaffDatePreferenceInput,
    StaffDetail,
    StaffInput,
)
from src.period_utils import period_dates
from src.ui_common import DB_PATH_ENV

APP = str(Path(__file__).resolve().parents[1] / "app.py")
LEADER, CHECKER, CLEANER = 1, 2, 3
START = "2026-11-02"  # 月曜
DATES = period_dates(START, 7)
MON, TUE, SAT = DATES[0], DATES[1], DATES[5]
WEEKDAYS, WEEKEND, ALL = [0, 1, 2, 3, 4], [5, 6], list(range(7))
IDS: dict[str, int] = {}  # fixture で作ったスタッフの id


def add(conn, code, name, role, weekdays, **kwargs):
    kwargs.setdefault("standard_start_time", "09:00")
    kwargs.setdefault("standard_end_time", "15:30")
    return repo.create_staff(conn, code, name, role, 3, weekdays=weekdays, **kwargs)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "coverage.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    connection = get_connection(path)
    initialize_database(connection)
    IDS.clear()
    IDS.update({
        "leader_weekday": add(connection, "1", "平日リーダー", LEADER, WEEKDAYS),
        "leader_weekend": add(connection, "2", "土日リーダー", LEADER, WEEKEND),
        "leader_tue": add(connection, "3", "火曜リーダー", LEADER, [1]),
        "checker": add(connection, "4", "チェッカー", CHECKER, ALL),
        "cleaner1": add(connection, "5", "清掃一", CLEANER, ALL),
        "cleaner2": add(connection, "6", "清掃二", CLEANER, ALL),
        "no_time": add(connection, "7", "時間未定", CLEANER, ALL,
                       standard_start_time=None, standard_end_time=None),
    })
    yield connection
    connection.close()


def save_requirements(conn, dates, total, roles):
    reqs = [DailyRequirementInput(d, total) for d in dates]
    role_rows = [RoleRequirementInput(d, rid, n) for d in dates for rid, n in roles.items()]
    assert not services.save_period_requirements(conn, dates, reqs, role_rows)


def absolute_off(conn, staff_id, work_date):
    assert not services.save_period_preferences(
        conn, staff_id, DATES, [StaffDatePreferenceInput(staff_id, work_date, absolute_off=True)]
    )


# ---------------------------------------------------------------------------
# 体制チェック
# ---------------------------------------------------------------------------


def test_no_problem_when_enough_people_can_come(conn):
    save_requirements(conn, DATES, 3, {LEADER: 1, CHECKER: 1, CLEANER: 1})
    assert problem_days(analyze_coverage(conn, DATES)) == []


def test_only_leader_on_a_day_off_is_reported_with_the_reason(conn):
    """月曜のリーダーは1人だけ. その人が必ず休みなら、作る前に「リーダーがいない」と分かる."""
    save_requirements(conn, DATES, 3, {LEADER: 1, CHECKER: 1, CLEANER: 1})
    absolute_off(conn, IDS["leader_weekday"], MON)

    days = problem_days(analyze_coverage(conn, DATES))
    assert [d.work_date for d in days] == [MON]
    assert describe_problems(days[0]) == ["リーダー：出られる人がいません（平日リーダーさんが必ず休み）"]


def test_another_leader_covers_tuesday(conn):
    save_requirements(conn, DATES, 3, {LEADER: 1, CHECKER: 1, CLEANER: 1})
    absolute_off(conn, IDS["leader_weekday"], TUE)
    assert problem_days(analyze_coverage(conn, [TUE])) == []


def test_role_count_shortfall_message(conn):
    save_requirements(conn, [MON], 4, {CLEANER: 3})
    day = analyze_coverage(conn, [MON])[0]
    assert describe_problems(day) == ["クリーナー：出られる人が2人です（必要3人）（勤務時間が未設定：時間未定さん）"]


def test_total_shortfall_message(conn):
    save_requirements(conn, [SAT], 6, {})
    day = analyze_coverage(conn, [SAT])[0]  # 土曜に出られるのは 土日リーダー・チェッカー・清掃一・清掃二
    assert describe_problems(day)[0] == "全体：出られる人が4人です（必要6人）（勤務時間が未設定：時間未定さん）"


def test_unsaved_days_are_checked_with_the_defaults(conn):
    """必要人数をまだ保存していない日は、初期値（平日7名・土日6名）で判定する."""
    day = analyze_coverage(conn, [MON])[0]
    assert day.uses_defaults is True
    assert day.required_total == 7
    assert day.has_problem  # 6人しかいない


def test_skill_shortfall(conn):
    assert not services.save_period_requirements(
        conn, [MON], [DailyRequirementInput(MON, 1, required_skill_level=4, required_skill_count=1)], []
    )
    day = analyze_coverage(conn, [MON])[0]
    assert "スキルLv4以上：出られる人が0人です（必要1人）" in describe_problems(day)


# ---------------------------------------------------------------------------
# 休み希望の警告（もしもの判定）
# ---------------------------------------------------------------------------


def test_absence_warning_for_the_last_leader(conn):
    save_requirements(conn, DATES, 3, {LEADER: 1, CHECKER: 1, CLEANER: 1})
    data = load_coverage_input(conn, DATES)
    assert absence_warning(data, IDS["leader_weekday"], MON) == (
        "11/2(月)に平日リーダーさんが休むと、出られるリーダーがいなくなります。"
    )


def test_no_absence_warning_when_someone_else_can_cover(conn):
    save_requirements(conn, DATES, 3, {LEADER: 1, CHECKER: 1, CLEANER: 1})
    data = load_coverage_input(conn, DATES)
    assert absence_warning(data, IDS["leader_weekday"], TUE) is None
    assert absence_warning(data, IDS["cleaner1"], MON) is None


def test_no_absence_warning_on_a_normal_day_off(conn):
    save_requirements(conn, DATES, 3, {LEADER: 1})
    data = load_coverage_input(conn, DATES)
    assert absence_warning(data, IDS["leader_weekday"], SAT) is None


def test_absence_warning_for_the_total(conn):
    save_requirements(conn, [MON], 5, {})
    data = load_coverage_input(conn, [MON])
    assert absence_warning(data, IDS["cleaner1"], MON) == (
        "11/2(月)に清掃一さんが休むと、出られる人が3人になり、必要な5人に足りません。"
    )


# ---------------------------------------------------------------------------
# 勤務案の不足の説明
# ---------------------------------------------------------------------------


def test_explain_shortage_with_a_reason(conn):
    save_requirements(conn, DATES, 3, {LEADER: 1, CHECKER: 1, CLEANER: 1})
    absolute_off(conn, IDS["leader_weekday"], MON)
    coverage = analyze_coverage(conn, [MON])[0]
    day = DailyGenerationResult(MON, "SHORTAGE", True, 2, required_total_staff=3,
                                staff_shortage=1, role_shortages={LEADER: 1})
    text = explain_shortage(day, coverage, {LEADER: "リーダー"})
    assert text == ("11/2(月)：リーダー1人不足・人数1人不足。"
                    "リーダー：出られる人がいません（平日リーダーさんが必ず休み）")


def test_explain_shortage_without_a_coverage_problem(conn):
    save_requirements(conn, [TUE], 3, {LEADER: 1})
    coverage = analyze_coverage(conn, [TUE])[0]
    day = DailyGenerationResult(TUE, "SHORTAGE", True, 3, required_total_staff=3,
                                role_shortages={LEADER: 1})
    text = explain_shortage(day, coverage, {LEADER: "リーダー"})
    assert text.startswith("11/3(火・祝)：リーダー1人不足。出られる人はいますが、連続勤務の上限")


# ---------------------------------------------------------------------------
# スタッフ設定の食い違い
# ---------------------------------------------------------------------------


def detail(weekdays, target=None, max_consecutive=None, start="09:00", end="15:30"):
    staff = StaffInput(1, "1", "太郎", LEADER, 3, standard_start_time=start, standard_end_time=end,
                       target_days_per_week=target, max_consecutive_days=max_consecutive)
    return StaffDetail(staff=staff, weekdays=tuple(weekdays))


@pytest.mark.parametrize(
    ("weekdays", "max_consecutive", "expected"),
    [(WEEKDAYS, 3, 4.0), (WEEKDAYS, None, 5.0), ([0, 2, 4], 1, 3.0), (ALL, 1, 3.5), ([], 3, 0.0)],
)
def test_max_days_per_week(weekdays, max_consecutive, expected):
    assert max_days_per_week(weekdays, max_consecutive) == expected


def test_target_above_what_the_weekdays_and_run_limit_allow():
    """10月のバックテストで見つかった例: 月〜金・連続3日まで・目安週5日 → 週4日までしか入れない."""
    assert staff_setting_warnings(detail(WEEKDAYS, target=5, max_consecutive=3)) == [
        "週に働く日数の目安が週5日ですが、通常勤務曜日（月火水木金）と連続3日までの上限では、週4日までしか入れません。"
    ]


def test_target_above_the_number_of_weekdays():
    assert staff_setting_warnings(detail([1, 3], target=3)) == [
        "週に働く日数の目安が週3日ですが、通常勤務曜日は週2日です。"
    ]


def test_consistent_settings_have_no_warning():
    assert staff_setting_warnings(detail(WEEKDAYS, target=4, max_consecutive=3)) == []


def test_missing_weekdays_and_hours():
    warnings = staff_setting_warnings(detail([], start=None, end=None))
    assert "通常勤務曜日が未設定のため、勤務表に入りません。" in warnings
    assert "通常勤務時間が未設定のため、勤務表に入りません。" in warnings


# ---------------------------------------------------------------------------
# 画面
# ---------------------------------------------------------------------------


def press(at, label=None, key=None):
    button = at.button(key=key) if key else next(b for b in at.button if b.label == label)
    button.click().run()
    assert not at.exception


PERIOD = period_dates(START, 14)


def open_step2():
    at = AppTest.from_file(APP, default_timeout=60).run()
    press(at, "勤務表をつくる")
    at.date_input[0].set_value(date.fromisoformat(START))
    at.selectbox[0].set_value(14)
    press(at, "この期間で始める")
    return at


def test_step2_shows_days_that_cannot_be_covered(conn):
    save_requirements(conn, PERIOD, 3, {LEADER: 1, CHECKER: 1, CLEANER: 1})
    absolute_off(conn, IDS["leader_weekday"], MON)
    at = open_step2()
    texts = [m.value for m in at.markdown]
    assert any("体制チェック：このままでは足りなくなる日（1日）" in t for t in texts)
    assert any("11/2(月)" in t and "平日リーダーさんが必ず休み" in t for t in texts)


def test_step2_says_ok_when_everyone_can_be_covered(conn):
    save_requirements(conn, PERIOD, 3, {LEADER: 1, CHECKER: 1, CLEANER: 1})
    at = open_step2()
    assert any("体制チェック：どの日も" in s.value for s in at.success)


def test_choosing_absolute_off_for_the_last_leader_shows_a_warning(conn):
    save_requirements(conn, PERIOD, 3, {LEADER: 1, CHECKER: 1, CLEANER: 1})
    at = open_step2()
    sid = IDS["leader_weekday"]
    at.selectbox(key="gw_pref_staff").set_value(sid).run()
    at.selectbox(key="gw_pref_date").set_value(MON).run()
    at.radio(key=f"gw_pref_{sid}_{MON}_off").set_value("必ず休み").run()
    assert any("出られるリーダーがいなくなります" in w.value for w in at.warning)


def test_staff_page_lists_setting_problems(conn):
    sid = add(conn, "8", "目安多め", LEADER, WEEKDAYS, target_days_per_week=5, max_consecutive_days=3)
    at = AppTest.from_file(str(Path(APP).parent / "pages" / "01_staff.py"), default_timeout=30).run()
    assert not at.exception
    assert any(e.label.startswith("設定を確認してください") for e in at.expander)
    texts = " ".join(m.value for m in at.markdown)
    assert "目安多め" in texts and "週4日までしか入れません" in texts
    assert "時間未定" in texts and "通常勤務時間が未設定" in texts
    at.selectbox(key="edit_target_staff").set_value(sid).run()
    assert any("週4日までしか入れません" in w.value for w in at.warning)
