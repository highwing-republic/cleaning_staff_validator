"""Streamlitページの起動・主要操作テスト（T60〜T84 UI）.

streamlit.testing.v1.AppTest でページを実行し、例外なくレンダリングできること、
主要な操作（生成・確定など）が動くことを確認する。ピクセル単位の検証は行わない。
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database
from src.models import DailyRequirementInput, MonthlyConditionInput
from src.month_utils import get_month_dates

YM = "2026-10"
LEADER, CHECKER, CLEANER = 1, 2, 3

REPO_ROOT = Path(__file__).resolve().parent.parent


def _page(relative_path: str) -> str:
    return str(REPO_ROOT / relative_path)


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setenv("STAFF_SHIFT_DB_PATH", str(path))
    # 初期化しておく（roles seed含む）
    conn = get_connection(str(path))
    initialize_database(conn)
    conn.close()
    return path


def _seed_staff_and_month(db_path, n_leader=1, n_checker=1, n_cleaner=2, required=2, target_days=10):
    conn = get_connection(str(db_path))
    initialize_database(conn)
    ids = []
    for i in range(n_leader):
        ids.append(repo.create_staff(conn, f"L{i}", LEADER, 480, 5))
    for i in range(n_checker):
        ids.append(repo.create_staff(conn, f"C{i}", CHECKER, 480, 5))
    for i in range(n_cleaner):
        ids.append(repo.create_staff(conn, f"W{i}", CLEANER, 480, 5))
    for sid in ids:
        repo.save_monthly_condition(conn, MonthlyConditionInput(sid, YM, target_days * 480))
    dates = get_month_dates(YM)
    repo.save_daily_requirements(conn, [DailyRequirementInput(d, required) for d in dates])
    conn.close()
    return ids


# ---------------------------------------------------------------------------
# 空DBでも例外なく起動すること
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "page",
    [
        "pages/01_staff.py",
        "pages/02_monthly_conditions.py",
        "pages/03_preferences.py",
        "pages/04_requirements.py",
        "pages/05_generate.py",
        "pages/06_schedule.py",
    ],
)
def test_page_runs_without_exception_on_empty_db(db_path, page):
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
    assert menu_paths == sorted(p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / "pages").glob("*.py"))
    assert all(not title.isascii() for _, title in app.MENU_PAGES)

    at = AppTest.from_file(_page("app.py"), default_timeout=30)
    at.run()
    for path in menu_paths:
        at.switch_page(path).run()
        assert not at.exception, path


# ---------------------------------------------------------------------------
# 01 スタッフ
# ---------------------------------------------------------------------------


def test_staff_page_renders_with_seeded_data(db_path):
    _seed_staff_and_month(db_path)
    at = AppTest.from_file(_page("pages/01_staff.py"), default_timeout=30)
    at.run()
    assert not at.exception


def test_staff_page_create_form_saves_skill_level(db_path):
    """SK06-SK07: 新規登録フォームでスキル5を選び、DBへ保存されること."""
    at = AppTest.from_file(_page("pages/01_staff.py"), default_timeout=30)
    at.run()
    assert not at.exception

    # 新規登録フォーム(一覧が空の状態): text_input[0]=スタッフ名, selectbox[1]=スキル
    at.text_input[0].input("新人太郎")
    at.selectbox[1].select(5)
    submit = [b for b in at.button if b.label == "登録"]
    assert submit, "create submit button not found"
    submit[0].click()
    at.run()
    assert not at.exception

    conn = get_connection(str(db_path))
    created = next(s for s in repo.list_staff(conn) if s.staff_name == "新人太郎")
    conn.close()
    assert created.skill_level == 5


def test_staff_page_edit_form_updates_skill_level(db_path):
    """SK08: 編集フォームでスキルを変更し、即座にDBへ保存されること."""
    conn = get_connection(str(db_path))
    staff_id = repo.create_staff(conn, "山田", CLEANER, 480, 5, 2)
    conn.close()

    at = AppTest.from_file(_page("pages/01_staff.py"), default_timeout=30)
    at.run()
    assert not at.exception

    # 編集フォーム: selectbox[4]=edit_skill_level（唯一の登録済みスタッフが選択済み）
    at.selectbox[4].select(5)
    submit = [b for b in at.button if b.label == "更新"]
    assert submit, "edit submit button not found"
    submit[0].click()
    at.run()
    assert not at.exception

    conn = get_connection(str(db_path))
    updated = repo.get_staff(conn, staff_id)
    conn.close()
    assert updated.skill_level == 5


# ---------------------------------------------------------------------------
# 02〜04 renders with seeded data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "page",
    [
        "pages/02_monthly_conditions.py",
        "pages/03_preferences.py",
        "pages/04_requirements.py",
    ],
)
def test_pages_render_with_seeded_data(db_path, page):
    _seed_staff_and_month(db_path)
    at = AppTest.from_file(_page(page), default_timeout=30)
    at.run()
    assert not at.exception


# ---------------------------------------------------------------------------
# 05 生成: ボタン押下でシフトが生成されること
# ---------------------------------------------------------------------------


def test_generate_page_creates_schedule(db_path):
    _seed_staff_and_month(db_path)
    at = AppTest.from_file(_page("pages/05_generate.py"), default_timeout=30)
    at.run()
    assert not at.exception

    # 「シフト生成を実行」ボタンをクリック
    buttons = [b for b in at.button if "シフト生成を実行" in (b.label or "")]
    assert buttons, "generate button not found"
    buttons[0].click().run()
    assert not at.exception

    conn = get_connection(str(db_path))
    month = repo.get_schedule_month(conn, YM)
    assert month is not None
    assert month.status == "DRAFT"
    assignments = repo.load_assignments(conn, YM)
    assert len(assignments) > 0
    conn.close()


# ---------------------------------------------------------------------------
# 06 シフト確認: 生成後に表が描画され、確定できること
# ---------------------------------------------------------------------------


def test_schedule_page_renders_and_confirms_after_generation(db_path):
    _seed_staff_and_month(db_path)
    conn = get_connection(str(db_path))
    outcome = services.generate_and_save(conn, YM)
    assert outcome.saved
    conn.close()

    at = AppTest.from_file(_page("pages/06_schedule.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert len(at.dataframe) > 0

    confirm_buttons = [b for b in at.button if "確定する" in (b.label or "")]
    assert confirm_buttons, "confirm button not found"
    confirm_buttons[0].click().run()
    assert not at.exception

    conn = get_connection(str(db_path))
    month = repo.get_schedule_month(conn, YM)
    assert month.status == "CONFIRMED"
    conn.close()

    # 確定後に再度開くと編集フォームが表示されない（ScheduleConfirmedErrorが表に出ない）こと
    at2 = AppTest.from_file(_page("pages/06_schedule.py"), default_timeout=30)
    at2.run()
    assert not at2.exception
    edit_forms = [f for f in at2.get("form") if "manual_edit_form" in f.key]
    assert edit_forms == []


def test_schedule_page_unconfirm_restores_editing(db_path):
    _seed_staff_and_month(db_path)
    conn = get_connection(str(db_path))
    assert services.generate_and_save(conn, YM).saved
    assert services.confirm_month(conn, YM).confirmed
    conn.close()

    at = AppTest.from_file(_page("pages/06_schedule.py"), default_timeout=30)
    at.run()
    assert not at.exception
    button = [b for b in at.button if b.label == "確定を解除する"][0]
    assert button.disabled  # 確認チェック前は押せない

    at.checkbox(key="unconfirm_agree").check().run()
    [b for b in at.button if b.label == "確定を解除する"][0].click().run()
    assert not at.exception

    conn = get_connection(str(db_path))
    assert repo.get_schedule_month(conn, YM).status == "DRAFT"
    conn.close()

    at2 = AppTest.from_file(_page("pages/06_schedule.py"), default_timeout=30)
    at2.run()
    assert not at2.exception
    assert [f for f in at2.get("form") if "manual_edit_form" in f.key]
