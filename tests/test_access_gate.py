"""閲覧パスワード（公開URLでスタッフの情報を見られないようにする）のテスト."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import access_gate
from src import repositories as repo
from src.database import get_connection, initialize_database
from src.ui_common import DB_PATH_ENV

APP = str(Path(__file__).resolve().parents[1] / "app.py")
HOME_BUTTON = "勤務表をつくる"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "gate.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    monkeypatch.delenv(access_gate.ENV_KEY, raising=False)
    monkeypatch.setattr(access_gate, "FAILURE_DELAY_SECONDS", 0.0)
    conn = get_connection(str(path))
    initialize_database(conn)
    repo.create_staff(conn, "0001", "実在さん", 3, 3, standard_start_time="09:00",
                      standard_end_time="15:30", weekdays=[0, 1, 2, 3, 4])
    conn.close()
    return path


def run(password=None):
    at = AppTest.from_file(APP, default_timeout=45)
    if password is not None:
        at.secrets["access"] = {"password": password}
    at.run()
    assert not at.exception
    return at


def buttons(at):
    return [b.label for b in at.button]


def test_without_password_the_app_opens(db):
    at = run()
    assert HOME_BUTTON in buttons(at)
    assert not at.text_input


def test_with_password_nothing_is_shown_until_it_is_entered(db):
    at = run("合言葉")
    assert HOME_BUTTON not in buttons(at)
    assert at.text_input(key="access_password_input")
    # ログイン画面しか登録されないので、ほかの画面へは移れない（URLを直接開いてもログイン画面になる）
    with pytest.raises(ValueError):
        at.switch_page("app_pages/01_staff.py")
    assert not at.dataframe


def test_wrong_password_is_rejected(db):
    at = run("合言葉")
    at.text_input(key="access_password_input").input("ちがう")
    at.button[0].click().run()
    assert any("パスワードが違います" in e.value for e in at.error)
    assert HOME_BUTTON not in buttons(at)


def test_right_password_opens_the_app(db):
    at = run("合言葉")
    at.text_input(key="access_password_input").input("合言葉")
    at.button[0].click().run()
    assert not at.exception
    assert HOME_BUTTON in buttons(at)
    at.switch_page("app_pages/01_staff.py").run()
    assert "実在さん" in list(at.dataframe[0].value["氏名"])


def test_changing_the_password_asks_again(db):
    """パスワードを変えたら、前のパスワードで開いていた画面でも入れ直しになる."""
    at = run("古い")
    at.session_state[access_gate.STATE_KEY] = access_gate._fingerprint("古い")
    at.secrets["access"] = {"password": "新しい"}
    at.run()
    assert HOME_BUTTON not in buttons(at)
    assert access_gate.check_password("a", "a") and not access_gate.check_password("a", "b")


def test_env_password_is_used(monkeypatch):
    monkeypatch.setenv(access_gate.ENV_KEY, " pw ")
    assert access_gate.configured_password() == "pw"


def test_page_files_are_not_auto_discovered():
    """画面のフォルダを pages にしない（Streamlit が自動で読み込み、パスワード確認を通らずに開けてしまう）."""
    root = Path(APP).parent
    assert not (root / "pages").exists()
    assert (root / "app_pages" / "01_staff.py").exists()


def test_a_page_opened_directly_shows_no_data(db):
    """app.py を通らずに画面を直接開いても、パスワード未入力ならデータを開かない."""
    page = str(Path(APP).parent / "app_pages" / "01_staff.py")
    at = AppTest.from_file(page, default_timeout=30)
    at.secrets["access"] = {"password": "合言葉"}
    at.run()
    assert not at.exception
    assert not at.dataframe
    assert any("パスワードを入力してから" in w.value for w in at.warning)


@pytest.mark.parametrize(("url_path", "title"), [
    ("staff", "スタッフ管理"),
    ("requirements", "予約・必要人数"),
    ("output", "勤務表出力"),
])
def test_opening_a_page_url_before_login_returns_there_after_login(db, url_path, title):
    """未ログインで /staff などを開くとログイン画面が出て（Page not found にしない）、ログイン後はその画面が開く."""
    from streamlit.util import calc_hash

    at = AppTest.from_file(APP, default_timeout=45)
    at.secrets["access"] = {"password": "合言葉"}
    at._page_hash = calc_hash(url_path)  # ブラウザでそのURLを開いたのと同じ
    at.run()
    assert not at.exception
    assert at.text_input(key="access_password_input")
    assert HOME_BUTTON not in buttons(at)

    at.text_input(key="access_password_input").input("合言葉")
    at.button[0].click().run()
    assert not at.exception
    assert HOME_BUTTON not in buttons(at)  # ホームではなく、開こうとした画面
    assert any(title in t.value for t in at.title)


def test_login_from_the_top_page_opens_home(db):
    at = run("合言葉")
    at.text_input(key="access_password_input").input("合言葉")
    at.button[0].click().run()
    assert HOME_BUTTON in buttons(at)
