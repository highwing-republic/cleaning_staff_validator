"""Streamlitページの起動・主要操作テスト.

streamlit.testing.v1.AppTest でページを実行し、例外なくレンダリングできること、
主要な操作（スタッフ登録・更新など）が動くことを確認する。ピクセル単位の検証は行わない。
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import repositories as repo
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
# 03 日別必要条件
# ---------------------------------------------------------------------------


def test_requirements_page_saves_skill_columns(db_path):
    at = AppTest.from_file(_page("pages/03_requirements.py"), default_timeout=30)
    at.run()
    assert not at.exception
    [b for b in at.button if b.label == "保存"][0].click()
    at.run()
    assert not at.exception

    conn = get_connection(str(db_path))
    ym = at.session_state["year_month"]
    reqs = repo.get_daily_requirements(conn, ym)
    conn.close()
    assert len(reqs) == len(get_month_dates(ym))
    assert all((r.required_skill_level, r.required_skill_count) == (None, 0) for r in reqs)
