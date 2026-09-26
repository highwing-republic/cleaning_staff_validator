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
