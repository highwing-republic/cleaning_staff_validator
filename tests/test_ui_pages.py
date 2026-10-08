"""Streamlitページの起動・主要操作テスト.

streamlit.testing.v1.AppTest でページを実行し、例外なくレンダリングできること、
主要な操作（スタッフ登録・更新など）が動くことを確認する。ピクセル単位の検証は行わない。
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from attendance_csv import make_csv
from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database
from src.models import DailyRequirementInput
from src.month_utils import get_month_dates
from src.ui_common import DB_PATH_ENV

YM = "2026-10"
LEADER, CHECKER, CLEANER = 1, 2, 3

REPO_ROOT = Path(__file__).resolve().parent.parent

PAGES = [
    "pages/01_staff.py",
    "pages/02_attendance_import.py",
    "pages/03_requirements.py",
    "pages/04_validation.py",
    "pages/05_preferences.py",
]


def _page(relative_path: str) -> str:
    return str(REPO_ROOT / relative_path)


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    # 元アプリの変数が設定されていても使われないこと
    monkeypatch.setenv("STAFF_SHIFT_DB_PATH", str(tmp_path / "source_app.db"))
    conn = get_connection(str(path))
    initialize_database(conn)
    conn.close()
    return path


def _seed(db_path):
    conn = get_connection(str(db_path))
    ids = [
        repo.create_staff(conn, "0001", "L0", LEADER, 5, "清掃"),
        repo.create_staff(conn, "0002", "C0", CHECKER, 4, "清掃"),
        repo.create_staff(conn, "0003", "W0", CLEANER, 3, "清掃"),
    ]
    repo.save_daily_requirements(conn, [DailyRequirementInput(d, 2) for d in get_month_dates(YM)])
    conn.close()
    return ids


def test_db_path_env_is_app_specific():
    assert DB_PATH_ENV == "CLEANING_STAFF_VALIDATOR_DB_PATH"


# ---------------------------------------------------------------------------
# 起動
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", PAGES)
def test_page_runs_without_exception_on_empty_db(db_path, page):
    at = AppTest.from_file(_page(page), default_timeout=30)
    at.run()
    assert not at.exception


@pytest.mark.parametrize("page", PAGES)
def test_pages_render_with_seeded_data(db_path, page):
    _seed(db_path)
    at = AppTest.from_file(_page(page), default_timeout=30)
    at.run()
    assert not at.exception


def test_app_main_runs(db_path):
    at = AppTest.from_file(_page("app.py"), default_timeout=30)
    at.run()
    assert not at.exception


def test_menu_lists_every_page_with_japanese_title(db_path):
    import app

    menu_paths = [path for path, _ in app.MENU_PAGES]
    assert menu_paths == PAGES
    assert menu_paths == sorted(p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / "pages").glob("*.py"))
    assert all(not title.isascii() for _, title in app.MENU_PAGES)

    at = AppTest.from_file(_page("app.py"), default_timeout=30)
    at.run()
    for path in menu_paths:
        at.switch_page(path).run()
        assert not at.exception, path


def test_ui_uses_app_specific_db(db_path, tmp_path):
    at = AppTest.from_file(_page("pages/01_staff.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert not (tmp_path / "source_app.db").exists()


# ---------------------------------------------------------------------------
# 01 スタッフ
# ---------------------------------------------------------------------------


def _create_form_submit(at, employee_code, name, skill=None):
    # 新規登録フォーム(一覧が空の状態): text_input[0]=従業員番号, [1]=氏名, [2]=部門
    at.text_input[0].input(employee_code)
    at.text_input[1].input(name)
    if skill is not None:
        at.selectbox[1].select(skill)  # selectbox[0]=ロール, [1]=スキル
    [b for b in at.button if b.label == "登録"][0].click()
    at.run()


def test_staff_page_create_form_saves_employee_code_as_text(db_path):
    at = AppTest.from_file(_page("pages/01_staff.py"), default_timeout=30)
    at.run()
    _create_form_submit(at, "0015", "新人太郎", skill=5)
    assert not at.exception

    conn = get_connection(str(db_path))
    created = repo.get_staff_by_employee_code(conn, "0015")
    conn.close()
    assert created is not None
    assert created.staff_name == "新人太郎"
    assert created.skill_level == 5
    assert created.department == "清掃"


def test_staff_page_rejects_duplicate_employee_code(db_path):
    conn = get_connection(str(db_path))
    repo.create_staff(conn, "0015", "既存", CLEANER)
    conn.close()

    at = AppTest.from_file(_page("pages/01_staff.py"), default_timeout=30)
    at.run()
    create_code = [t for t in at.text_input if t.label == "従業員番号" and t.key != "edit_employee_code"][0]
    create_name = [t for t in at.text_input if t.label == "氏名" and t.key != "edit_name"][0]
    create_code.input("0015")
    create_name.input("重複")
    [b for b in at.button if b.label == "登録"][0].click()
    at.run()
    assert not at.exception
    assert any("すでに登録されています" in e.value for e in at.error)

    conn = get_connection(str(db_path))
    assert [s.staff_name for s in repo.list_staff(conn)] == ["既存"]
    conn.close()


def test_staff_page_edit_form_updates_skill_and_code(db_path):
    conn = get_connection(str(db_path))
    staff_id = repo.create_staff(conn, "0001", "山田", CLEANER, 2)
    conn.close()

    at = AppTest.from_file(_page("pages/01_staff.py"), default_timeout=30)
    at.run()
    assert not at.exception

    at.text_input(key="edit_employee_code").input("0100")
    at.selectbox(key="edit_skill_level").select(5)
    [b for b in at.button if b.label == "更新"][0].click()
    at.run()
    assert not at.exception

    conn = get_connection(str(db_path))
    updated = repo.get_staff(conn, staff_id)
    conn.close()
    assert updated.skill_level == 5
    assert updated.employee_code == "0100"


# ---------------------------------------------------------------------------
# 01 スタッフ: 通常勤務条件・特殊スキル（Phase 5）
# ---------------------------------------------------------------------------

STAFF_PAGE = "pages/01_staff.py"


def _open_staff_page():
    at = AppTest.from_file(_page(STAFF_PAGE), default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _heavy_work_id(db_path):
    conn = get_connection(str(db_path))
    skill_id = repo.list_special_skills(conn)[0].special_skill_id
    conn.close()
    return skill_id


def test_staff_page_shows_weekday_checkboxes(db_path):
    at = _open_staff_page()
    labels = [c.label for c in at.checkbox]
    for label in ("月", "火", "水", "木", "金", "土", "日"):
        assert label in labels


def test_staff_page_shows_standard_time_inputs(db_path):
    at = _open_staff_page()
    labels = [t.label for t in at.text_input]
    assert "通常開始時刻" in labels
    assert "通常終了時刻" in labels


def test_staff_page_shows_work_volume_inputs(db_path):
    at = _open_staff_page()
    labels = [n.label for n in at.number_input]
    assert "目標勤務日数 / 週（任意）" in labels
    assert "期間内最大勤務日数（任意）" in labels
    assert "最大連続勤務日数（任意）" in labels


def test_staff_page_offers_heavy_work_special_skill(db_path):
    at = _open_staff_page()
    multiselects = [m for m in at.multiselect if m.label == "特殊スキル"]
    assert multiselects
    assert any("力仕事可" in str(m.options) for m in multiselects)


def test_staff_page_creates_staff_with_standard_conditions(db_path):
    """新規登録で通常勤務条件・曜日・特殊スキルがまとめて保存されること."""
    heavy_work = _heavy_work_id(db_path)
    at = _open_staff_page()

    at.text_input[0].input("0007")
    at.text_input[1].input("Aさん")
    at.selectbox[1].select(4)                                     # 総合スキル
    at.text_input(key="create_start").input("09:00")
    at.text_input(key="create_end").input("15:30")
    at.number_input(key="create_target_days").set_value(4)
    at.number_input(key="create_max_consecutive").set_value(5)
    # format_func付きのmultiselectでは set_value で表示名を渡す
    at.multiselect(key="create_special_skills").set_value(["力仕事可"])
    for weekday in (0, 1, 3, 4, 5):
        at.checkbox(key=f"create_weekday_{weekday}").check()
    [b for b in at.button if b.label == "登録"][0].click()
    at.run()
    assert not at.exception

    conn = get_connection(str(db_path))
    created = repo.get_staff_by_employee_code(conn, "0007")
    detail = repo.get_staff_detail(conn, created.staff_id)
    conn.close()

    assert detail.staff.standard_start_time == "09:00"
    assert detail.staff.standard_end_time == "15:30"
    assert detail.staff.target_days_per_week == 4
    assert detail.staff.max_consecutive_days == 5
    assert detail.staff.max_days_per_period is None
    assert detail.weekdays == (0, 1, 3, 4, 5)
    assert detail.special_skill_ids == (heavy_work,)


def test_staff_page_creates_short_time_part_timer(db_path):
    """通常から他のスタッフより早く終わるパートを登録できること."""
    at = _open_staff_page()
    at.text_input[0].input("0008")
    at.text_input[1].input("Bさん")
    at.text_input(key="create_start").input("09:00")
    at.text_input(key="create_end").input("13:00")
    [b for b in at.button if b.label == "登録"][0].click()
    at.run()
    assert not at.exception

    conn = get_connection(str(db_path))
    created = repo.get_staff_by_employee_code(conn, "0008")
    conn.close()
    assert (created.standard_start_time, created.standard_end_time) == ("09:00", "13:00")


def test_staff_page_rejects_end_before_start(db_path):
    at = _open_staff_page()
    at.text_input[0].input("0009")
    at.text_input[1].input("Cさん")
    at.text_input(key="create_start").input("15:00")
    at.text_input(key="create_end").input("09:00")
    [b for b in at.button if b.label == "登録"][0].click()
    at.run()
    assert not at.exception
    assert any("終了時刻は開始時刻より後" in e.value for e in at.error)

    conn = get_connection(str(db_path))
    assert repo.get_staff_by_employee_code(conn, "0009") is None
    conn.close()


def test_staff_page_rejects_start_time_only(db_path):
    at = _open_staff_page()
    at.text_input[0].input("0010")
    at.text_input[1].input("Dさん")
    at.text_input(key="create_start").input("09:00")
    [b for b in at.button if b.label == "登録"][0].click()
    at.run()
    assert not at.exception
    assert any("開始・終了の両方" in e.value for e in at.error)

    conn = get_connection(str(db_path))
    assert repo.get_staff_by_employee_code(conn, "0010") is None
    conn.close()


def test_staff_page_allows_creation_without_standard_conditions(db_path):
    """勤務条件が未確定のスタッフも登録できること（不明は不明のまま残す）."""
    at = _open_staff_page()
    at.text_input[0].input("0011")
    at.text_input[1].input("Eさん")
    [b for b in at.button if b.label == "登録"][0].click()
    at.run()
    assert not at.exception

    conn = get_connection(str(db_path))
    created = repo.get_staff_by_employee_code(conn, "0011")
    detail = repo.get_staff_detail(conn, created.staff_id)
    conn.close()
    assert detail.staff.standard_start_time is None
    assert detail.staff.target_days_per_week is None
    assert detail.weekdays == ()
    assert detail.special_skill_ids == ()


def test_staff_page_created_staff_appears_in_list(db_path):
    at = _open_staff_page()
    at.text_input[0].input("0012")
    at.text_input[1].input("Fさん")
    at.text_input(key="create_start").input("09:00")
    at.text_input(key="create_end").input("15:30")
    for weekday in (0, 2, 4):
        at.checkbox(key=f"create_weekday_{weekday}").check()
    [b for b in at.button if b.label == "登録"][0].click()
    at.run()
    assert not at.exception

    table = at.dataframe[0].value
    assert "0012" in list(table["従業員番号"])
    row = table[table["従業員番号"] == "0012"].iloc[0]
    # 内部値（0,2,4）ではなく人が読める形で表示する
    assert row["通常勤務曜日"] == "月・水・金"
    assert row["通常勤務時間"] == "09:00-15:30（390分）"


def test_staff_list_shows_special_skill_names(db_path):
    heavy_work = _heavy_work_id(db_path)
    conn = get_connection(str(db_path))
    repo.create_staff(
        conn, "0013", "Gさん", CLEANER, 3, "清掃", special_skill_ids=[heavy_work]
    )
    conn.close()

    at = _open_staff_page()
    table = at.dataframe[0].value
    row = table[table["従業員番号"] == "0013"].iloc[0]
    assert row["特殊スキル"] == "力仕事可"


def test_staff_list_shows_unset_target_days_explicitly(db_path):
    conn = get_connection(str(db_path))
    repo.create_staff(conn, "0014", "Hさん", CLEANER, 3, "清掃")
    conn.close()

    at = _open_staff_page()
    table = at.dataframe[0].value
    row = table[table["従業員番号"] == "0014"].iloc[0]
    assert row["目標日数/週"] == "未設定"


def test_staff_page_edit_form_updates_standard_conditions(db_path):
    heavy_work = _heavy_work_id(db_path)
    conn = get_connection(str(db_path))
    staff_id = repo.create_staff(
        conn, "0020", "Iさん", CLEANER, 3, "清掃",
        standard_start_time="09:00", standard_end_time="15:30",
        target_days_per_week=5, weekdays=[0, 1, 3], special_skill_ids=[heavy_work],
    )
    conn.close()

    at = _open_staff_page()
    at.text_input(key="edit_end").input("13:00")
    at.number_input(key="edit_target_days").set_value(3)
    at.checkbox(key="edit_weekday_1").uncheck()
    at.checkbox(key="edit_weekday_5").check()
    at.multiselect(key="edit_special_skills").set_value([])
    [b for b in at.button if b.label == "更新"][0].click()
    at.run()
    assert not at.exception

    conn = get_connection(str(db_path))
    detail = repo.get_staff_detail(conn, staff_id)
    conn.close()
    assert detail.staff.standard_end_time == "13:00"
    assert detail.staff.target_days_per_week == 3
    assert detail.weekdays == (0, 3, 5)
    assert detail.special_skill_ids == ()


def test_staff_page_edit_form_prefills_existing_conditions(db_path):
    conn = get_connection(str(db_path))
    repo.create_staff(
        conn, "0021", "Jさん", CLEANER, 3, "清掃",
        standard_start_time="09:30", standard_end_time="14:30",
        target_days_per_week=4, weekdays=[0, 2],
    )
    conn.close()

    at = _open_staff_page()
    assert at.text_input(key="edit_start").value == "09:30"
    assert at.text_input(key="edit_end").value == "14:30"
    assert at.number_input(key="edit_target_days").value == 4
    assert at.checkbox(key="edit_weekday_0").value is True
    assert at.checkbox(key="edit_weekday_1").value is False
    assert at.checkbox(key="edit_weekday_2").value is True


# ---------------------------------------------------------------------------
# 03 予約・必要人数（Phase 7）
# ---------------------------------------------------------------------------

REQUIREMENT_PAGE = "pages/03_requirements.py"
REQUIREMENT_SAVE_BUTTON = "この期間の予約・必要人数を保存"


def _open_requirement_page():
    at = AppTest.from_file(_page(REQUIREMENT_PAGE), default_timeout=120)
    at.run()
    assert not at.exception
    return at


def _requirement_dates(at):
    from src.period_utils import period_dates

    start = at.session_state["requirement_period_start"]
    days = at.session_state["requirement_period_days"]
    return period_dates(start.isoformat(), int(days))


def _save_requirements(at):
    [b for b in at.button if b.label == REQUIREMENT_SAVE_BUTTON][0].click()
    at.run()
    assert not at.exception
    return at


def _saved_requirement(db_path, work_date):
    conn = get_connection(str(db_path))
    req = repo.get_daily_requirement(conn, work_date)
    conn.close()
    return req


def test_requirements_page_opens(db_path):
    at = _open_requirement_page()
    assert any("予約" in str(t.value) for t in at.title)


def test_requirements_page_offers_period_selection(db_path):
    at = _open_requirement_page()
    assert [d.label for d in at.date_input] == ["開始日"]
    assert any(s.label == "期間" for s in at.selectbox)


def test_requirements_period_can_be_shortened(db_path):
    at = _open_requirement_page()
    at.selectbox(key="requirement_period_days").select(10)
    at.run()
    assert not at.exception
    assert len(_requirement_dates(at)) == 10


def test_requirements_period_never_exceeds_fourteen_days(db_path):
    from src.period_utils import PERIOD_MAX_DAYS

    at = _open_requirement_page()
    options = at.selectbox(key="requirement_period_days").options
    day_counts = [int(str(o).replace("日間", "")) for o in options]
    assert max(day_counts) == PERIOD_MAX_DAYS
    assert len(_requirement_dates(at)) <= PERIOD_MAX_DAYS


def test_requirements_period_can_cross_month_boundary(db_path):
    """月をまたぐ期間を扱えること."""
    import datetime

    at = _open_requirement_page()
    at.date_input(key="requirement_period_start").set_value(datetime.date(2026, 10, 28))
    at.run()
    assert not at.exception
    dates = _requirement_dates(at)
    assert dates[0] == "2026-10-28"
    assert any(d.startswith("2026-11") for d in dates)


def test_requirements_page_edits_whole_period_without_navigation(db_path):
    at = _open_requirement_page()
    for work_date in _requirement_dates(at):
        assert at.checkbox(key=f"req_{work_date}_defined") is not None
        assert at.number_input(key=f"req_{work_date}_reserved") is not None
        assert at.number_input(key=f"req_{work_date}_required") is not None


def test_requirements_unset_days_create_no_rows(db_path):
    """設定をONにしない日は要件未設定のまま（行を作らない）."""
    at = _open_requirement_page()
    dates = _requirement_dates(at)
    _save_requirements(at)

    conn = get_connection(str(db_path))
    assert repo.list_daily_requirements(conn, dates[0], dates[-1]) == []
    conn.close()


def test_requirements_saves_reserved_rooms_and_required_staff(db_path):
    at = _open_requirement_page()
    target = _requirement_dates(at)[0]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_reserved").set_value(8)
    at.number_input(key=f"req_{target}_required").set_value(4)
    _save_requirements(at)

    req = _saved_requirement(db_path, target)
    assert req is not None
    assert req.reserved_rooms == 8
    assert req.required_total_staff == 4


def test_requirements_saves_role_conditions(db_path):
    at = _open_requirement_page()
    target = _requirement_dates(at)[1]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_required").set_value(5)
    at.number_input(key=f"req_{target}_role_{LEADER}").set_value(1)
    at.number_input(key=f"req_{target}_role_{CHECKER}").set_value(2)
    _save_requirements(at)

    conn = get_connection(str(db_path))
    counts = {
        r.role_id: r.required_count
        for r in repo.list_role_requirements(conn, target, target)
    }
    conn.close()
    assert counts[LEADER] == 1
    assert counts[CHECKER] == 2


def test_requirements_saves_skill_condition(db_path):
    at = _open_requirement_page()
    target = _requirement_dates(at)[2]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_required").set_value(5)
    at.number_input(key=f"req_{target}_skill_level").set_value(4)
    at.number_input(key=f"req_{target}_skill_count").set_value(2)
    _save_requirements(at)

    req = _saved_requirement(db_path, target)
    assert (req.required_skill_level, req.required_skill_count) == (4, 2)


def test_requirements_saves_without_skill_condition(db_path):
    at = _open_requirement_page()
    target = _requirement_dates(at)[3]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_required").set_value(3)
    _save_requirements(at)

    req = _saved_requirement(db_path, target)
    assert (req.required_skill_level, req.required_skill_count) == (None, 0)


def test_requirements_saves_max_total_staff(db_path):
    at = _open_requirement_page()
    target = _requirement_dates(at)[4]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_required").set_value(3)
    at.number_input(key=f"req_{target}_max").set_value(6)
    _save_requirements(at)

    assert _saved_requirement(db_path, target).max_total_staff == 6


def test_requirements_required_zero_is_a_defined_requirement(db_path):
    """required_total_staff=0 は「設定済み・必要人数0」であり要件未設定ではない."""
    at = _open_requirement_page()
    target = _requirement_dates(at)[5]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_required").set_value(0)
    _save_requirements(at)

    req = _saved_requirement(db_path, target)
    assert req is not None
    assert req.required_total_staff == 0


def test_requirements_reserved_rooms_zero_differs_from_blank(db_path):
    """予約0室と未入力を区別して保存できること."""
    at = _open_requirement_page()
    dates = _requirement_dates(at)
    zero_day, blank_day = dates[6], dates[7]
    for work_date in (zero_day, blank_day):
        at.checkbox(key=f"req_{work_date}_defined").check()
        at.number_input(key=f"req_{work_date}_required").set_value(3)
    at.number_input(key=f"req_{zero_day}_reserved").set_value(0)
    _save_requirements(at)

    assert _saved_requirement(db_path, zero_day).reserved_rooms == 0
    assert _saved_requirement(db_path, blank_day).reserved_rooms is None


def test_requirements_turning_off_removes_requirement_and_roles(db_path):
    """設定OFFにした日は要件行もロール行も削除される."""
    at = _open_requirement_page()
    target = _requirement_dates(at)[0]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_required").set_value(4)
    at.number_input(key=f"req_{target}_role_{LEADER}").set_value(1)
    _save_requirements(at)
    assert _saved_requirement(db_path, target) is not None

    at.checkbox(key=f"req_{target}_defined").uncheck()
    _save_requirements(at)

    conn = get_connection(str(db_path))
    assert repo.get_daily_requirement(conn, target) is None
    assert repo.list_role_requirements(conn, target, target) == []
    conn.close()


def test_requirements_saves_whole_period_at_once(db_path):
    at = _open_requirement_page()
    dates = _requirement_dates(at)
    for index, work_date in enumerate(dates[:5]):
        at.checkbox(key=f"req_{work_date}_defined").check()
        at.number_input(key=f"req_{work_date}_reserved").set_value(index + 1)
        at.number_input(key=f"req_{work_date}_required").set_value(index)
    _save_requirements(at)

    conn = get_connection(str(db_path))
    saved = repo.list_daily_requirements(conn, dates[0], dates[-1])
    conn.close()
    assert [r.work_date for r in saved] == dates[:5]
    assert [r.reserved_rooms for r in saved] == [1, 2, 3, 4, 5]


def test_requirements_summary_counts_defined_and_undefined(db_path):
    at = _open_requirement_page()
    target = _requirement_dates(at)[0]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_reserved").set_value(8)
    at.number_input(key=f"req_{target}_required").set_value(4)
    _save_requirements(at)

    metrics = {m.label: m.value for m in at.metric}
    assert metrics["設定済み日数"] == "1 / 14"
    assert metrics["未設定日数"] == "13"
    assert metrics["予約室数合計"] == "8室"


def test_requirements_overview_distinguishes_undefined_days(db_path):
    at = _open_requirement_page()
    target = _requirement_dates(at)[0]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_required").set_value(0)
    _save_requirements(at)

    table = at.dataframe[0].value
    assert list(table["必要人数"])[0] == "0名"
    assert list(table["必要人数"])[1] == "要件未設定"


def test_requirements_shows_success_message_after_save(db_path):
    at = _open_requirement_page()
    _save_requirements(at)
    assert any("保存しました" in s.value for s in at.success)


def test_requirements_rejects_required_over_max(db_path):
    at = _open_requirement_page()
    target = _requirement_dates(at)[0]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_required").set_value(8)
    at.number_input(key=f"req_{target}_max").set_value(3)
    _save_requirements(at)

    assert at.error
    assert _saved_requirement(db_path, target) is None


def test_requirements_rejects_skill_count_without_level(db_path):
    at = _open_requirement_page()
    target = _requirement_dates(at)[0]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_required").set_value(3)
    at.number_input(key=f"req_{target}_skill_count").set_value(2)
    _save_requirements(at)

    assert at.error
    assert _saved_requirement(db_path, target) is None


def test_requirements_does_not_change_data_outside_period(db_path):
    conn = get_connection(str(db_path))
    repo.save_daily_requirement(
        conn, DailyRequirementInput(work_date="2026-01-15", required_total_staff=9)
    )
    conn.close()

    at = _open_requirement_page()
    target = _requirement_dates(at)[0]
    at.checkbox(key=f"req_{target}_defined").check()
    at.number_input(key=f"req_{target}_required").set_value(4)
    _save_requirements(at)

    assert _saved_requirement(db_path, "2026-01-15").required_total_staff == 9


def test_requirements_csv_import_section_remains(db_path):
    """既存の月単位CSV取り込みを壊さないこと."""
    at = _open_requirement_page()
    assert at.file_uploader
    assert any(s.label == "対象年月" for s in at.selectbox)


# ---------------------------------------------------------------------------
# 05 勤務希望入力（Phase 6）
# ---------------------------------------------------------------------------

PREFERENCE_PAGE = "pages/05_preferences.py"
PREFERENCE_SAVE_BUTTON = "このスタッフの希望を保存"


def _seed_preference_staff(db_path):
    """通常勤務曜日・通常勤務時間を持つスタッフを登録する.

    2名目は通常勤務時間が未設定（勤務条件が未確定のスタッフでも画面が壊れないこと用）。
    """
    conn = get_connection(str(db_path))
    full = repo.create_staff(
        conn, "0101", "常勤Aさん", CLEANER, 4, "清掃",
        standard_start_time="09:00", standard_end_time="15:30",
        weekdays=[0, 1, 2, 3, 4, 5, 6],
    )
    no_time = repo.create_staff(
        conn, "0102", "時間未設定Bさん", CLEANER, 3, "清掃", weekdays=[0, 1, 2, 3, 4, 5, 6]
    )
    part = repo.create_staff(
        conn, "0103", "短時間Cさん", CLEANER, 3, "清掃",
        standard_start_time="09:00", standard_end_time="13:00",
        weekdays=[0, 1, 3, 4],
    )
    conn.close()
    return full, no_time, part


def _open_preference_page():
    at = AppTest.from_file(_page(PREFERENCE_PAGE), default_timeout=60)
    at.run()
    assert not at.exception
    return at


def _period_dates_of(at):
    """画面が対象にしている期間の日付一覧（ウィジェットキー生成用）."""
    from src.period_utils import period_dates

    start = at.session_state["preference_period_start"]
    days = at.session_state["preference_period_days"]
    return period_dates(start.isoformat(), int(days))


def _save_preferences(at):
    [b for b in at.button if b.label == PREFERENCE_SAVE_BUTTON][0].click()
    at.run()
    assert not at.exception
    return at


def _saved(db_path, staff_id, work_date):
    conn = get_connection(str(db_path))
    pref = repo.get_staff_date_preference(conn, staff_id, work_date)
    conn.close()
    return pref


def test_preference_page_runs_without_staff(db_path):
    """スタッフ未登録でも画面が落ちないこと."""
    at = AppTest.from_file(_page(PREFERENCE_PAGE), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("スタッフ" in i.value for i in at.info)


def test_preference_page_offers_period_selection(db_path):
    _seed_preference_staff(db_path)
    at = _open_preference_page()
    assert [d.label for d in at.date_input] == ["開始日"]
    assert any(s.label == "期間" for s in at.selectbox)


def test_preference_period_never_exceeds_fourteen_days(db_path):
    """最大14日まで（選択肢にも14日を超えるものを出さない）."""
    from src.period_utils import PERIOD_MAX_DAYS

    _seed_preference_staff(db_path)
    at = _open_preference_page()
    # options は format_func 適用後（"10日間" など）なので数値部分だけを見る
    options = at.selectbox(key="preference_period_days").options
    day_counts = [int(str(o).replace("日間", "")) for o in options]
    assert day_counts
    assert max(day_counts) == PERIOD_MAX_DAYS
    assert len(_period_dates_of(at)) <= PERIOD_MAX_DAYS


def test_preference_period_can_be_shortened(db_path):
    _seed_preference_staff(db_path)
    at = _open_preference_page()
    at.selectbox(key="preference_period_days").select(10)
    at.run()
    assert not at.exception
    assert len(_period_dates_of(at)) == 10


def test_preference_period_can_cross_month_boundary(db_path):
    """月をまたぐ期間を扱えること."""
    import datetime

    _seed_preference_staff(db_path)
    at = _open_preference_page()
    at.date_input(key="preference_period_start").set_value(datetime.date(2026, 10, 28))
    at.run()
    assert not at.exception
    dates = _period_dates_of(at)
    assert dates[0] == "2026-10-28"
    assert any(d.startswith("2026-11") for d in dates)


def test_preference_page_lets_staff_be_selected(db_path):
    full, no_time, part = _seed_preference_staff(db_path)
    at = _open_preference_page()
    selector = at.selectbox(key="preference_staff")
    assert selector.value == full
    at.selectbox(key="preference_staff").select(part)
    at.run()
    assert not at.exception
    assert at.selectbox(key="preference_staff").value == part


def test_preference_page_shows_staff_by_date_grid(db_path):
    """スタッフ × 日付の一覧が出ること."""
    _seed_preference_staff(db_path)
    at = _open_preference_page()
    table = at.dataframe[0].value
    assert list(table["スタッフ"]) == ["常勤Aさん", "時間未設定Bさん", "短時間Cさん"]
    assert len(table.columns) == len(_period_dates_of(at)) + 1


def test_preference_grid_shows_normal_for_unchanged_days(db_path):
    """変更なしの日は「未入力」ではなく「通常」と表示する."""
    _seed_preference_staff(db_path)
    at = _open_preference_page()
    table = at.dataframe[0].value
    row = table[table["スタッフ"] == "常勤Aさん"].iloc[0]
    values = [row[c] for c in table.columns if c != "スタッフ"]
    assert set(values) == {"通常"}
    assert "未入力" not in values


def test_preference_grid_shows_normal_off_weekdays(db_path):
    """通常休み曜日が分かる表示になっていること."""
    _seed_preference_staff(db_path)
    at = _open_preference_page()
    table = at.dataframe[0].value
    row = table[table["スタッフ"] == "短時間Cさん"].iloc[0]
    values = [row[c] for c in table.columns if c != "スタッフ"]
    assert "通常休み" in values


def test_preference_page_shows_standard_work_time(db_path):
    """早上がり入力時に比較できるよう通常勤務時間を出すこと."""
    _seed_preference_staff(db_path)
    at = _open_preference_page()
    assert any("09:00-15:30" in str(c.value) for c in at.caption)


def test_preference_page_warns_when_standard_time_unset(db_path):
    """通常勤務時間が未設定でも画面は壊さず、確定できないことを警告する."""
    full, no_time, part = _seed_preference_staff(db_path)
    at = _open_preference_page()
    at.selectbox(key="preference_staff").select(no_time)
    at.run()
    assert not at.exception
    assert any("通常勤務時間が未設定" in w.value for w in at.warning)


def test_preference_page_edits_whole_period_without_navigation(db_path):
    """1スタッフの期間分（14日）をページ遷移なしでまとめて編集できること."""
    _seed_preference_staff(db_path)
    at = _open_preference_page()
    dates = _period_dates_of(at)
    for work_date in dates:
        assert at.checkbox(key=f"pref_{work_date}_absolute_off") is not None
        assert at.text_input(key=f"pref_{work_date}_end") is not None


def test_preference_saves_absolute_off(db_path):
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[2]
    at.checkbox(key=f"pref_{target}_absolute_off").check()
    _save_preferences(at)

    pref = _saved(db_path, full, target)
    assert pref is not None
    assert pref.absolute_off is True
    assert pref.prefer_off is False


def test_preference_saves_prefer_off(db_path):
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[3]
    at.checkbox(key=f"pref_{target}_prefer_off").check()
    _save_preferences(at)

    pref = _saved(db_path, full, target)
    assert pref.prefer_off is True
    assert pref.absolute_off is False


def test_preference_saves_early_leave(db_path):
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[4]
    at.text_input(key=f"pref_{target}_end").input("13:00")
    _save_preferences(at)

    pref = _saved(db_path, full, target)
    assert pref.override_end_time == "13:00"
    assert pref.override_start_time is None


def test_preference_saves_late_start(db_path):
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[5]
    at.text_input(key=f"pref_{target}_start").input("10:00")
    _save_preferences(at)

    pref = _saved(db_path, full, target)
    assert pref.override_start_time == "10:00"
    assert pref.override_end_time is None


def test_preference_saves_both_time_overrides(db_path):
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[6]
    at.text_input(key=f"pref_{target}_start").input("10:00")
    at.text_input(key=f"pref_{target}_end").input("13:00")
    _save_preferences(at)

    pref = _saved(db_path, full, target)
    assert (pref.override_start_time, pref.override_end_time) == ("10:00", "13:00")


def test_preference_saves_available_extra(db_path):
    """通常休み曜日に「通常外だが勤務可能」を保存できること."""
    _, _, part = _seed_preference_staff(db_path)
    at = _open_preference_page()
    at.selectbox(key="preference_staff").select(part)
    at.run()
    dates = _period_dates_of(at)
    # 短時間Cさんは水(2)・土(5)・日(6)が通常休み
    import datetime

    target = next(
        d for d in dates if datetime.date.fromisoformat(d).weekday() in (2, 5, 6)
    )
    at.checkbox(key=f"pref_{target}_available_extra").check()
    _save_preferences(at)

    pref = _saved(db_path, part, target)
    assert pref.available_extra is True


def test_preference_saves_note_only(db_path):
    """備考だけの登録も許可する."""
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[7]
    at.text_input(key=f"pref_{target}_note").input("通院")
    _save_preferences(at)

    pref = _saved(db_path, full, target)
    assert pref.note == "通院"
    assert pref.absolute_off is False
    assert pref.prefer_off is False


def test_preference_saves_absolute_off_with_note(db_path):
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[8]
    at.checkbox(key=f"pref_{target}_absolute_off").check()
    at.text_input(key=f"pref_{target}_note").input("家族送迎")
    _save_preferences(at)

    pref = _saved(db_path, full, target)
    assert pref.absolute_off is True
    assert pref.note == "家族送迎"


def test_preference_saves_prefer_off_with_early_leave(db_path):
    """「できれば休み。勤務するなら13時まで」を1日で保存できること."""
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[9]
    at.checkbox(key=f"pref_{target}_prefer_off").check()
    at.text_input(key=f"pref_{target}_end").input("13:00")
    _save_preferences(at)

    pref = _saved(db_path, full, target)
    assert pref.prefer_off is True
    assert pref.override_end_time == "13:00"


def test_preference_saves_several_days_at_once(db_path):
    """紙から複数日をまとめて転記できること."""
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    dates = _period_dates_of(at)
    at.checkbox(key=f"pref_{dates[1]}_absolute_off").check()
    at.checkbox(key=f"pref_{dates[2]}_prefer_off").check()
    at.text_input(key=f"pref_{dates[3]}_end").input("13:00")
    at.text_input(key=f"pref_{dates[4]}_note").input("学校行事")
    _save_preferences(at)

    conn = get_connection(str(db_path))
    saved = repo.list_staff_preferences(conn, full, dates[0], dates[-1])
    conn.close()
    assert [p.work_date for p in saved] == dates[1:5]


def test_preference_unchanged_days_are_not_stored(db_path):
    """通常どおりの日はレコードを作らない."""
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    dates = _period_dates_of(at)
    at.checkbox(key=f"pref_{dates[1]}_absolute_off").check()
    _save_preferences(at)

    conn = get_connection(str(db_path))
    saved = repo.list_staff_preferences(conn, full, dates[0], dates[-1])
    conn.close()
    assert [p.work_date for p in saved] == [dates[1]]


def test_preference_back_to_normal_removes_row(db_path):
    """すべて通常に戻した日は行が削除される."""
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[2]
    at.checkbox(key=f"pref_{target}_absolute_off").check()
    _save_preferences(at)
    assert _saved(db_path, full, target) is not None

    at.checkbox(key=f"pref_{target}_absolute_off").uncheck()
    _save_preferences(at)
    assert _saved(db_path, full, target) is None


def test_preference_saved_value_is_shown_on_reload(db_path):
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[2]
    at.text_input(key=f"pref_{target}_end").input("13:00")
    _save_preferences(at)

    reopened = _open_preference_page()
    assert reopened.text_input(key=f"pref_{target}_end").value == "13:00"
    table = reopened.dataframe[0].value
    row = table[table["スタッフ"] == "常勤Aさん"].iloc[0]
    assert "13:00まで" in [row[c] for c in table.columns if c != "スタッフ"]


def test_preference_shows_success_message_after_save(db_path):
    _seed_preference_staff(db_path)
    at = _open_preference_page()
    at.checkbox(key=f"pref_{_period_dates_of(at)[2]}_absolute_off").check()
    _save_preferences(at)
    assert any("保存しました" in s.value for s in at.success)


def test_preference_rejects_absolute_off_with_time_override(db_path):
    """絶対休みと勤務時刻の変更は同時に指定できない."""
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[2]
    at.checkbox(key=f"pref_{target}_absolute_off").check()
    at.text_input(key=f"pref_{target}_end").input("13:00")
    _save_preferences(at)

    assert any("絶対休み" in e.value for e in at.error)
    assert _saved(db_path, full, target) is None


def test_preference_rejects_invalid_time_format(db_path):
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[2]
    at.text_input(key=f"pref_{target}_end").input("13時")
    _save_preferences(at)

    assert any("HH:MM" in e.value for e in at.error)
    assert _saved(db_path, full, target) is None


def test_preference_rejects_reversed_effective_time(db_path):
    """通常09:00始業に対し遅出16:00は勤務時間がなくなるため保存しない."""
    full, _, _ = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[2]
    at.text_input(key=f"pref_{target}_start").input("16:00")
    _save_preferences(at)

    assert at.error
    assert _saved(db_path, full, target) is None


def test_preference_switching_staff_does_not_carry_unsaved_input(db_path):
    """未保存の入力が別スタッフの画面に残らないこと."""
    full, no_time, part = _seed_preference_staff(db_path)
    at = _open_preference_page()
    target = _period_dates_of(at)[2]
    at.checkbox(key=f"pref_{target}_absolute_off").check()
    at.run()

    at.selectbox(key="preference_staff").select(part)
    at.run()
    assert not at.exception
    assert at.checkbox(key=f"pref_{target}_absolute_off").value is False
    _save_preferences(at)
    assert _saved(db_path, part, target) is None
    assert _saved(db_path, full, target) is None


# ---------------------------------------------------------------------------
# 02 勤怠CSV取込
# ---------------------------------------------------------------------------

IMPORT_PAGE = "pages/02_attendance_import.py"
IMPORT_BUTTON = "この内容を有効版として取り込む"


def _import_counts(db_path):
    conn = get_connection(str(db_path))
    counts = (
        conn.execute("SELECT COUNT(*) FROM attendance_imports").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM attendance_shifts").fetchone()[0],
    )
    conn.close()
    return counts


def _open_import_page():
    at = AppTest.from_file(_page(IMPORT_PAGE), default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _upload(at, data, name="shift_2026-09.csv"):
    at.file_uploader[0].upload(name, data, "text/csv")
    at.run()
    assert not at.exception
    return at


def _import_buttons(at):
    return [b for b in at.button if b.label == IMPORT_BUTTON]


def _metrics(at):
    return {m.label: m.value for m in at.metric}


def _all_text(at):
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption]
    parts += [e.value for e in at.info] + [e.value for e in at.warning]
    parts += [e.value for e in at.success] + [e.value for e in at.error]
    return "\n".join(str(p) for p in parts)


def test_import_page_before_upload(db_path):
    at = _open_import_page()
    assert _import_buttons(at) == []
    assert "有効な取込はまだありません" in _all_text(at)
    assert len(at.file_uploader) == 1


def test_import_page_preview_does_not_save(db_path):
    at = _upload(_open_import_page(), make_csv())
    metrics = _metrics(at)
    assert metrics["対象月"] == "2026年9月"
    assert metrics["従業員数"] == "3"
    assert metrics["入力済みシフトセル数"] == "90"
    assert metrics["清掃所属人数"] == "2"
    assert metrics["未登録スタッフ数"] == "3"
    assert metrics["UNKNOWN勤務セル数"] == "0"
    assert "shift_2026-09.csv" in _all_text(at)
    # 未登録スタッフ一覧が確認できる
    assert any("未登録スタッフ" in e.label for e in at.expander)

    (button,) = _import_buttons(at)
    assert not button.disabled
    assert _import_counts(db_path) == (0, 0)


def test_import_page_fatal_error_blocks_saving(db_path):
    at = _upload(_open_import_page(), make_csv(extra_columns=["備考"]))
    assert at.error, "fatal error should be shown"
    buttons = _import_buttons(at)
    assert buttons and all(b.disabled for b in buttons)
    assert _import_counts(db_path) == (0, 0)


def test_import_page_saves_and_shows_active(db_path):
    _seed(db_path)  # 0001〜0003 は CSV にないので全員未登録
    at = _upload(_open_import_page(), make_csv())
    _import_buttons(at)[0].click()
    at.run()
    assert not at.exception

    assert _import_counts(db_path) == (1, 90)
    text = _all_text(at)
    assert "取込が完了しました" in text and "2026年9月の有効版を更新しました" in text
    # アップロード欄がリセットされ、同じ内容を二重に取り込めない
    assert _import_buttons(at) == []

    active_table = at.dataframe[0].value
    assert list(active_table["ファイル名"]) == ["shift_2026-09.csv"]
    assert list(active_table["対象月"]) == ["2026年9月"]
    assert list(active_table["未登録人数"]) == [3]


def test_import_page_reimport_notice_and_supersede(db_path):
    conn = get_connection(str(db_path))
    services.import_attendance(
        conn, services.preview_attendance_csv(conn, make_csv(), "old.csv")
    )
    conn.close()

    at = _upload(_open_import_page(), make_csv(), "new.csv")
    assert "2026年9月には既に有効な取込があります" in _all_text(at)
    assert "現在の取込は履歴として残り" in _all_text(at)

    _import_buttons(at)[0].click()
    at.run()
    assert not at.exception

    conn = get_connection(str(db_path))
    history = repo.list_attendance_imports(conn, "2026-09")
    conn.close()
    assert [(h.source_filename, h.status) for h in history] == [
        ("new.csv", "ACTIVE"),
        ("old.csv", "SUPERSEDED"),
    ]


# ---------------------------------------------------------------------------
# 04 日別検証
# ---------------------------------------------------------------------------

VALIDATION_PAGE = "pages/04_validation.py"
SEP = get_month_dates("2026-09")


def _seed_validation(db_path):
    """2026-09: OK 28日 / ERROR 1日(9/2) / 要件未設定 1日(9/3). 2026-10 も取込のみ行う."""
    conn = get_connection(str(db_path))
    repo.create_staff(conn, "0015", "テスト清掃A", LEADER, 4, "清掃")
    for ym, name in (("2026-09", "sep.csv"), ("2026-10", "oct.csv")):
        services.import_attendance(conn, services.preview_attendance_csv(conn, make_csv(ym), name))
    repo.save_daily_requirements(
        conn, [DailyRequirementInput(d, 2 if d == SEP[1] else 1) for d in SEP if d != SEP[2]]
    )
    conn.close()


def _open_validation_page():
    at = AppTest.from_file(_page(VALIDATION_PAGE), default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _table_dates(at):
    return list(at.dataframe[0].value["日付"]) if len(at.dataframe) else []


def test_validation_page_without_active_import(db_path):
    at = _open_validation_page()
    assert "勤怠シフトが取り込まれていません" in _all_text(at)
    assert len(at.dataframe) == 0


def test_validation_page_month_selection_defaults_to_latest(db_path):
    _seed_validation(db_path)
    at = _open_validation_page()
    select = at.selectbox(key="validation_year_month")
    assert list(select.options) == ["2026年10月", "2026年9月"]
    assert _metrics(at)["対象月"] == "2026年10月"
    assert "oct.csv" in _all_text(at)

    select.set_value("2026-09").run()
    assert not at.exception
    metrics = _metrics(at)
    assert metrics["対象月"] == "2026年9月"
    assert (metrics["OK日数"], metrics["WARNING日数"], metrics["ERROR日数"]) == ("28", "1", "1")
    assert metrics["要件未設定日数"] == "1"
    assert "sep.csv" in _all_text(at)


def _open_september(db_path):
    _seed_validation(db_path)
    at = _open_validation_page()
    at.selectbox(key="validation_year_month").set_value("2026-09").run()
    assert not at.exception
    return at


def test_validation_page_all_days_table(db_path):
    at = _open_september(db_path)
    table = at.dataframe[0].value
    assert len(table) == 30
    # ロール列は role master から生成される
    assert {"リーダー", "チェッカー", "クリーナー"} <= set(table.columns)
    by_date = table.set_index("日付")
    assert by_date.loc["9月1日(火)", "判定"] == "🟢 OK"
    assert by_date.loc["9月2日(水)", "判定"] == "🔴 ERROR"
    assert by_date.loc["9月3日(木)", "必要"] == "要件未設定"
    assert by_date.loc["9月3日(木)", "判定"] == "🟡 WARNING"
    assert by_date.loc["9月1日(火)", "清掃勤務"] == 1


def test_validation_page_problem_days_filter(db_path):
    at = _open_september(db_path)
    at.radio(key="validation_filter").set_value("問題のある日だけ").run()
    assert not at.exception
    assert _table_dates(at) == ["9月2日(水)", "9月3日(木)"]


def test_validation_page_errors_only_filter(db_path):
    at = _open_september(db_path)
    at.radio(key="validation_filter").set_value("ERRORのみ").run()
    assert not at.exception
    assert _table_dates(at) == ["9月2日(水)"]


def test_validation_page_issue_details(db_path):
    at = _open_september(db_path)
    labels = [e.label for e in at.expander]
    assert any(label.startswith("9月2日(水)") and "ERROR" in label for label in labels)
    assert any(label.startswith("9月3日(木)") and "WARNING" in label for label in labels)
    errors = [e.value for e in at.error]
    assert any("清掃スタッフが1名以上不足しています" in m and "必要：2名" in m for m in errors)
    warnings = [e.value for e in at.warning]
    assert any("必要条件が未設定" in m for m in warnings)


def _download_buttons(at):
    return at.get("download_button")


def test_validation_page_shows_excel_download(db_path):
    at = _open_september(db_path)
    (button,) = _download_buttons(at)
    assert button.proto.label == "Excelをダウンロード"
    assert all("Excelの作成に失敗" not in e.value for e in at.error)


def test_validation_page_excel_failure_does_not_crash(db_path, monkeypatch):
    import src.validation_excel as validation_excel

    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(validation_excel, "export_validation_excel", _boom)
    at = _open_september(db_path)
    assert _download_buttons(at) == []
    assert any("Excelの作成に失敗しました" in e.value for e in at.error)
    # 検証結果の表示は続く
    assert len(at.dataframe[0].value) == 30


def test_validation_page_without_import_has_no_excel_download(db_path):
    at = _open_validation_page()
    assert _download_buttons(at) == []
