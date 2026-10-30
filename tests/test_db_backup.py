"""データベースの自動の控え（GitHub の非公開リポジトリ）のテスト.

GitHub には実際に接続しない。偽物のクライアント（FakeRemote）に差し替えて、
- 起動時に手元が空なら控えから戻す
- 内容が変わったときだけ控えを送る
- 控えを読めないときは送らない（空のデータで控えを上書きしない）
を確かめる。後半は GitHub の API 呼び出し部分と、画面への組み込み。
"""

import base64
import io
import json
import sqlite3
import ssl
import urllib.error
from datetime import timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import db_backup, guided_workflow
from src import repositories as repo
from src.database import get_connection, initialize_database
from src.db_backup import (
    BACKUP_FAILED_MESSAGE,
    RESTORE_FAILED_MESSAGE,
    BackupConfig,
    BackupConflict,
    BackupError,
    GitHubContentsClient,
    config_from_mapping,
)
from src.ui_common import DB_PATH_ENV, open_connection

APP = str(Path(__file__).resolve().parents[1] / "app.py")
CONFIG = BackupConfig(repo="owner/cleaning-staff-data", token="secret-token")
SECRETS = {"data_backup": {"repo": CONFIG.repo, "token": CONFIG.token}}


class FakeRemote:
    """非公開リポジトリの代わり. 1ファイルだけを持つ."""

    def __init__(self) -> None:
        self.data: bytes | None = None
        self.sha: str | None = None
        self.downloads = 0
        self.uploads: list[tuple[bytes, str | None, str]] = []
        self.fail_download = False
        self.fail_upload = False

    def put(self, data: bytes) -> None:
        """ほかの場所で控えが更新された状態を作る."""
        self.data = data
        self.sha = f"external-{len(data)}-{self.downloads}"


class FakeClient:
    def __init__(self, remote: FakeRemote) -> None:
        self.remote = remote

    def download(self):
        self.remote.downloads += 1
        if self.remote.fail_download:
            raise BackupError("GitHub returned 500 on download")
        return self.remote.data, self.remote.sha

    def upload(self, data, sha, message):
        if self.remote.fail_upload:
            raise BackupError("GitHub returned 500 on upload")
        if sha != self.remote.sha:
            raise BackupConflict("GitHub returned 409 on upload")
        self.remote.uploads.append((data, sha, message))
        self.remote.data = data
        self.remote.sha = f"sha-{len(self.remote.uploads)}"
        return self.remote.sha


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "data" / "app.db"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    return path


@pytest.fixture
def remote(monkeypatch, db_file):
    db_backup.reset_state()
    fake = FakeRemote()
    monkeypatch.setattr(db_backup, "client_factory", lambda config: FakeClient(fake))
    monkeypatch.setattr(db_backup, "RESTORE_BACKOFF_SECONDS", (0.0, 0.0))  # テストでは待たない
    yield fake
    db_backup.reset_state()


def make_db(path: Path, *names: str) -> bytes:
    """スタッフを登録したデータベース（アプリで一度開いたもの）を作り、その中身を返す."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(path)
    initialize_database(conn)
    guided_workflow.initialize(conn)
    for index, name in enumerate(names, start=1):
        repo.create_staff(conn, f"E{index:03d}", name, 3, 3)
    conn.close()
    return path.read_bytes()


def staff_names(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[0] for row in conn.execute("SELECT staff_name FROM staff ORDER BY staff_id")]
    finally:
        conn.close()


def add_staff(name: str) -> None:
    conn = open_connection()
    repo.create_staff(conn, name, name, 3, 3)
    conn.close()


def open_app_once() -> None:
    """ホーム画面を開いたときと同じく、接続して初期化だけする."""
    conn = open_connection()
    guided_workflow.initialize(conn)
    conn.close()


# ---------------------------------------------------------------------------
# 設定の読み取り
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "section",
    [None, {}, {"repo": "owner/data"}, {"token": "t"}, {"repo": "data-only", "token": "t"},
     {"repo": "  ", "token": "t"}],
)
def test_incomplete_settings_turn_the_backup_off(section):
    assert config_from_mapping(section) is None


def test_settings_with_defaults():
    config = config_from_mapping({"repo": " owner/data ", "token": " t "})
    assert config == BackupConfig(repo="owner/data", token="t", branch=None, path="app.db")


def test_settings_with_branch_and_path():
    config = config_from_mapping(
        {"repo": "owner/data", "token": "t", "branch": "main", "path": "/backup/app.db"}
    )
    assert (config.branch, config.path) == ("main", "backup/app.db")


def test_config_repr_does_not_show_the_token():
    """設定をログや画面に出してもトークンが見えないようにする."""
    assert "secret-token" not in repr(CONFIG)


# ---------------------------------------------------------------------------
# 起動時の書き戻し
# ---------------------------------------------------------------------------


def test_without_settings_nothing_happens(remote, db_file, monkeypatch):
    """PC版（start.cmd）では Secrets が無いので、これまでどおり手元のファイルだけを使う."""
    monkeypatch.setattr(db_backup, "load_config", lambda: None)
    db_backup.ensure_restored()
    open_app_once()
    assert db_backup.backup_if_changed() is False
    assert db_backup.status() == {
        "enabled": False, "error": None, "restore_failed": False, "last_saved_at": None,
    }
    assert remote.downloads == 0


def test_restores_the_backup_when_the_server_has_no_database(remote, db_file, tmp_path):
    """再起動でファイルが消えても、控えからスタッフ登録が戻る."""
    remote.put(make_db(tmp_path / "saved.db", "山田", "佐藤"))
    assert not db_file.exists()

    db_backup.ensure_restored(CONFIG)

    assert staff_names(db_file) == ["山田", "佐藤"]
    assert db_backup.status()["enabled"] is True
    assert not db_file.with_suffix(".db.restoring").exists()


def test_restored_data_is_not_sent_back_unchanged(remote, db_file, tmp_path):
    """画面を開くだけ（初期化で連番が進む）では控えを送らない."""
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    db_backup.ensure_restored(CONFIG)
    for _ in range(3):
        open_app_once()
        assert db_backup.backup_if_changed() is False
    assert remote.uploads == []


def test_backup_from_an_older_version_is_sent_once_after_upgrade(remote, db_file, tmp_path):
    """アプリの更新で表が増えたときは、増えた状態を一度だけ控える."""
    old = tmp_path / "old.db"
    conn = get_connection(old)
    initialize_database(conn)  # かんたん作成の表がまだ無い版
    conn.close()
    remote.put(old.read_bytes())

    db_backup.ensure_restored(CONFIG)
    open_app_once()
    assert db_backup.backup_if_changed() is True
    open_app_once()
    assert db_backup.backup_if_changed() is False
    assert len(remote.uploads) == 1


def test_first_use_creates_the_backup(remote, db_file):
    """控えがまだ無いとき（初回）は、作ったデータベースをそのまま控える."""
    db_backup.ensure_restored(CONFIG)
    assert db_backup.status()["enabled"] is True
    open_app_once()

    assert db_backup.backup_if_changed() is True
    data, sha, message = remote.uploads[0]
    assert sha is None
    assert data[:16] == b"SQLite format 3\x00"
    assert message.startswith("backup: app.db (") and message.endswith(" JST)")


def test_existing_local_database_is_kept(remote, db_file, tmp_path):
    """手元にデータベースがあるとき（再起動していない）は、控えで上書きしない."""
    make_db(db_file, "手元")
    remote.put(make_db(tmp_path / "saved.db", "控え"))
    saved_sha = remote.sha

    db_backup.ensure_restored(CONFIG)
    assert staff_names(db_file) == ["手元"]
    assert db_backup.backup_if_changed() is True
    assert remote.uploads[0][1] == saved_sha  # 控えの sha を付けて更新した
    assert staff_names_from_bytes(remote.data, tmp_path) == ["手元"]


def staff_names_from_bytes(data: bytes, tmp_path: Path) -> list[str]:
    path = tmp_path / "check.db"
    path.write_bytes(data)
    return staff_names(path)


def test_empty_database_left_on_the_server_is_replaced_by_the_backup(remote, db_file, tmp_path):
    """控えを読めない間に空のデータベースができ、そのあと上書き更新で起動し直した場合も、
    空のデータベースを「サーバーに残っていたデータ」として控えへ送らず、控えから戻す."""
    make_db(db_file)  # スタッフ0人
    remote.put(make_db(tmp_path / "saved.db", "山田", "佐藤"))

    db_backup.ensure_restored(CONFIG)
    assert staff_names(db_file) == ["山田", "佐藤"]
    open_app_once()
    assert db_backup.backup_if_changed() is False
    assert remote.uploads == []


def test_empty_database_is_never_uploaded_over_a_backup_with_staff(remote, db_file, tmp_path):
    """最後の安全装置: スタッフのいる控えを、スタッフ0人のデータベースで上書きしない."""
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    db_backup.ensure_restored(CONFIG)
    db_file.unlink()
    make_db(db_file)  # 何かの理由で手元が空になった
    saved = remote.data

    assert db_backup.backup_if_changed() is False
    assert remote.data == saved
    assert db_backup.status()["error"] == BACKUP_FAILED_MESSAGE


def test_first_staff_can_be_backed_up_when_the_backup_is_empty(remote, db_file, tmp_path):
    """控えにまだスタッフがいない（使い始め）なら、空のデータベースも控えてよい."""
    remote.put(make_db(tmp_path / "saved.db"))
    db_backup.ensure_restored(CONFIG)
    add_staff("田中")
    assert db_backup.backup_if_changed() is True
    assert staff_names_from_bytes(remote.data, tmp_path) == ["田中"]


def test_restore_is_only_attempted_once_per_process(remote, db_file, tmp_path):
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    db_backup.ensure_restored(CONFIG)
    db_backup.ensure_restored(CONFIG)
    db_backup.ensure_restored(CONFIG)
    assert remote.downloads == 1


def test_settings_added_later_start_the_backup(remote, db_file, monkeypatch):
    """マージのあとで Secrets を設定しても、再起動を待たずに次の画面表示から控え始める."""
    settings = {"config": None}
    monkeypatch.setattr(db_backup, "load_config", lambda: settings["config"])
    db_backup.ensure_restored()
    open_app_once()
    add_staff("田中")
    assert db_backup.backup_if_changed() is False

    settings["config"] = CONFIG
    db_backup.ensure_restored()
    assert db_backup.status()["enabled"] is True
    assert db_backup.backup_if_changed() is True  # 控えがまだ無いので、手元の内容を控える
    assert remote.uploads


def test_database_created_before_the_settings_does_not_overwrite_the_backup(
    remote, db_file, tmp_path, monkeypatch
):
    """設定が無い間に作られた空のデータベースで、控えのスタッフ登録を消さない."""
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    settings = {"config": None}
    monkeypatch.setattr(db_backup, "load_config", lambda: settings["config"])
    db_backup.ensure_restored()  # 起動時には手元にデータベースが無い
    open_app_once()

    settings["config"] = CONFIG
    db_backup.ensure_restored()
    assert staff_names(db_file) == ["山田"]
    assert db_backup.backup_if_changed() is False
    assert remote.uploads == []


# ---------------------------------------------------------------------------
# 控えを読めないとき
# ---------------------------------------------------------------------------


def test_restore_failure_stops_the_backup(remote, db_file, tmp_path):
    """控えを読めないまま空のデータを送ると、控えのスタッフ登録が消えてしまう."""
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    saved = remote.data
    remote.fail_download = True

    db_backup.ensure_restored(CONFIG)
    open_app_once()
    add_staff("新人")

    assert db_backup.backup_if_changed() is False
    assert remote.uploads == []
    assert remote.data == saved
    state = db_backup.status()
    assert state["enabled"] is False
    assert state["restore_failed"] is True
    assert state["error"] == RESTORE_FAILED_MESSAGE


def test_restore_is_retried_after_a_while(remote, db_file, tmp_path, monkeypatch):
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    remote.fail_download = True
    start = db_backup._now()
    monkeypatch.setattr(db_backup, "_now", lambda: start)
    db_backup.ensure_restored(CONFIG)
    open_app_once()  # 失敗している間に空のデータベースができてしまっても
    remote.fail_download = False

    attempts = remote.downloads  # 1回の画面表示の中では、何回か読み直してからあきらめる
    assert attempts == db_backup.RESTORE_ATTEMPTS
    db_backup.ensure_restored(CONFIG)  # あきらめたあと、すぐには読み直さない
    assert remote.downloads == attempts

    later = start + timedelta(seconds=db_backup.RESTORE_RETRY_SECONDS + 1)
    monkeypatch.setattr(db_backup, "_now", lambda: later)
    db_backup.ensure_restored(CONFIG)

    assert remote.downloads == attempts + 1
    assert staff_names(db_file) == ["山田"]  # 控えの内容に入れ替わる
    assert db_backup.status()["enabled"] is True
    assert db_backup.status()["error"] is None


def test_restore_retries_within_one_page_view(remote, db_file, tmp_path, monkeypatch):
    """起動直後の一時的な通信エラーなら、その場の読み直しで戻る."""
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    failures = {"left": 2}
    original = FakeClient.download

    def flaky(self):
        if failures["left"]:
            failures["left"] -= 1
            self.remote.downloads += 1
            raise BackupError("network error: URLError: temporary failure")
        return original(self)

    monkeypatch.setattr(FakeClient, "download", flaky)
    db_backup.ensure_restored(CONFIG)
    assert remote.downloads == 3
    assert staff_names(db_file) == ["山田"]
    assert db_backup.status()["enabled"] is True


def test_retry_now_skips_the_wait(remote, db_file, tmp_path):
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    remote.fail_download = True
    db_backup.ensure_restored(CONFIG)
    remote.fail_download = False
    db_backup.ensure_restored(CONFIG)
    assert db_backup.status()["restore_failed"] is True  # 待ち時間中は読み直さない

    db_backup.retry_now()
    db_backup.ensure_restored(CONFIG)
    assert staff_names(db_file) == ["山田"]
    assert db_backup.status()["restore_failed"] is False


def test_log_lines_explain_what_happened(remote, db_file, tmp_path, capsys, monkeypatch):
    """Streamlit Cloud のログで、戻せたか・なぜ戻せなかったかが分かる（トークンは出さない）."""
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    db_backup.ensure_restored(CONFIG)
    err = capsys.readouterr().err
    assert "data backup: restored" in err and CONFIG.repo in err

    db_backup.reset_state()
    monkeypatch.setattr(db_backup, "load_config", lambda: None)
    db_backup.ensure_restored()
    db_backup.ensure_restored()
    err = capsys.readouterr().err
    assert err.count("settings [data_backup] not found") == 1  # 毎回は出さない

    db_backup.reset_state()
    remote.fail_download = True
    db_backup.ensure_restored(CONFIG)
    err = capsys.readouterr().err
    assert "restore failed (attempt 1/3): GitHub returned 500" in err
    assert "secret-token" not in err


def test_broken_backup_does_not_replace_the_database(remote, db_file):
    remote.put(b"this is not a database")
    db_backup.ensure_restored(CONFIG)
    assert not db_file.exists()
    assert not db_file.with_suffix(".db.restoring").exists()
    assert db_backup.status()["restore_failed"] is True


# ---------------------------------------------------------------------------
# 控えを送る
# ---------------------------------------------------------------------------


def test_changes_are_sent(remote, db_file, tmp_path):
    db_backup.ensure_restored(CONFIG)
    open_app_once()
    db_backup.backup_if_changed()

    add_staff("田中")
    assert db_backup.backup_if_changed() is True
    assert staff_names_from_bytes(remote.data, tmp_path) == ["田中"]
    assert remote.uploads[1][1] == "sha-1"  # 直前の控えの sha を付けて更新する
    assert db_backup.status()["last_saved_at"] is not None


def test_upload_failure_is_reported_and_retried(remote, db_file, tmp_path):
    db_backup.ensure_restored(CONFIG)
    open_app_once()
    remote.fail_upload = True

    assert db_backup.backup_if_changed() is False
    state = db_backup.status()
    assert state["error"] == BACKUP_FAILED_MESSAGE
    assert state["restore_failed"] is False

    remote.fail_upload = False
    assert db_backup.backup_if_changed() is True  # 次の操作のときに送り直す
    assert db_backup.status()["error"] is None


def test_conflict_is_retried_with_the_latest_sha(remote, db_file, tmp_path):
    db_backup.ensure_restored(CONFIG)
    open_app_once()
    db_backup.backup_if_changed()
    remote.put(make_db(tmp_path / "other.db", "別の画面"))

    add_staff("田中")
    assert db_backup.backup_if_changed() is True
    assert staff_names_from_bytes(remote.data, tmp_path) == ["田中"]


def test_backup_errors_do_not_break_the_page(remote, db_file, monkeypatch):
    """想定外の失敗でも例外を外へ出さない（画面の再実行を止めないため）."""
    db_backup.ensure_restored(CONFIG)
    open_app_once()

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(db_backup, "_copy_database", boom)
    assert db_backup.backup_if_changed() is False
    assert db_backup.status()["error"] == BACKUP_FAILED_MESSAGE


# ---------------------------------------------------------------------------
# GitHub の API 呼び出し
# ---------------------------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __init__(self, status: int, payload: bytes) -> None:
        super().__init__(payload)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture
def http(monkeypatch):
    """urlopen を差し替え、送ったリクエストと返す応答を記録する."""
    calls: list = []
    responses: list = []

    def fake_urlopen(request, timeout, context=None):
        assert isinstance(context, ssl.SSLContext)  # 証明書の設定を必ず渡す
        calls.append(request)
        status, payload = responses.pop(0)
        if isinstance(status, Exception):
            raise status
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(payload))
        return FakeResponse(status, payload)

    monkeypatch.setattr(db_backup.urllib.request, "urlopen", fake_urlopen)
    return calls, responses


def contents_payload(data: bytes, sha: str = "abc") -> bytes:
    return json.dumps(
        {"sha": sha, "encoding": "base64", "content": base64.b64encode(data).decode()}
    ).encode()


def test_download_decodes_the_file(http):
    calls, responses = http
    responses.append((200, contents_payload(b"DB", "abc")))
    assert GitHubContentsClient(CONFIG).download() == (b"DB", "abc")
    request = calls[0]
    assert request.full_url == "https://api.github.com/repos/owner/cleaning-staff-data/contents/app.db"
    assert request.get_header("Authorization") == "Bearer secret-token"


def test_download_large_file_uses_the_raw_content(http):
    """1MB を超えるファイルは中身が空で返るので、生データを取り直す."""
    calls, responses = http
    responses.append((200, json.dumps({"sha": "big", "encoding": "none", "content": ""}).encode()))
    responses.append((200, b"RAWDB"))
    assert GitHubContentsClient(CONFIG).download() == (b"RAWDB", "big")
    assert calls[1].get_header("Accept") == "application/vnd.github.raw"


def test_download_with_branch(http):
    calls, responses = http
    responses.append((200, contents_payload(b"DB")))
    config = BackupConfig(repo="owner/data", token="t", branch="backup")
    GitHubContentsClient(config).download()
    assert calls[0].full_url.endswith("/contents/app.db?ref=backup")


def test_missing_file_in_a_reachable_repository_means_first_use(http):
    calls, responses = http
    responses.append((404, b"{}"))
    responses.append((200, b"{}"))
    assert GitHubContentsClient(CONFIG).download() == (None, None)
    assert calls[1].full_url == "https://api.github.com/repos/owner/cleaning-staff-data"


def test_unreachable_repository_is_an_error(http):
    """名前の誤り・権限不足も 404 になる. 初回と取り違えて空のデータで上書きしない."""
    calls, responses = http
    responses.append((404, b"{}"))
    responses.append((404, b"{}"))
    with pytest.raises(BackupError, match="not reachable"):
        GitHubContentsClient(CONFIG).download()


def test_download_server_error(http):
    _, responses = http
    responses.append((500, b"{}"))
    with pytest.raises(BackupError):
        GitHubContentsClient(CONFIG).download()


def test_network_error_does_not_leak_the_token(http):
    _, responses = http
    responses.append((urllib.error.URLError("secret-token in message"), b""))
    with pytest.raises(BackupError) as info:
        GitHubContentsClient(CONFIG).download()
    assert "secret-token" not in str(info.value)
    assert info.value.__cause__ is None


def test_network_error_keeps_the_reason(http):
    """証明書の失敗などの理由がログで分かるようにする."""
    _, responses = http
    reason = ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer certificate")
    responses.append((urllib.error.URLError(reason), b""))
    with pytest.raises(BackupError, match="certificate verify failed"):
        GitHubContentsClient(CONFIG).download()


def test_ssl_context_adds_certifi_when_available():
    """新しい Python 環境で OS の証明書が見つからなくても、certifi の証明書で GitHub に接続できる."""
    certifi = pytest.importorskip("certifi")
    db_backup._SSL_CONTEXT = None
    context = db_backup._ssl_context()
    plain = ssl.create_default_context()
    plain.load_verify_locations(cafile=certifi.where())
    assert context.cert_store_stats()["x509_ca"] >= plain.cert_store_stats()["x509_ca"] > 0
    assert db_backup._ssl_context() is context  # 毎回作り直さない


def test_upload_sends_base64_with_sha(http):
    calls, responses = http
    responses.append((200, json.dumps({"content": {"sha": "new"}}).encode()))
    config = BackupConfig(repo="owner/data", token="t", branch="backup")
    assert GitHubContentsClient(config).upload(b"DB", "old", "backup: test") == "new"
    request = calls[0]
    assert request.get_method() == "PUT"
    body = json.loads(request.data)
    assert body == {
        "message": "backup: test",
        "content": base64.b64encode(b"DB").decode(),
        "sha": "old",
        "branch": "backup",
    }


def test_first_upload_has_no_sha(http):
    calls, responses = http
    responses.append((201, json.dumps({"content": {"sha": "new"}}).encode()))
    GitHubContentsClient(CONFIG).upload(b"DB", None, "backup: test")
    assert "sha" not in json.loads(calls[0].data)


@pytest.mark.parametrize("status", [409, 422])
def test_upload_conflict(http, status):
    _, responses = http
    responses.append((status, b"{}"))
    with pytest.raises(BackupConflict):
        GitHubContentsClient(CONFIG).upload(b"DB", "old", "m")


def test_upload_permission_error(http):
    _, responses = http
    responses.append((403, b"{}"))
    with pytest.raises(BackupError) as info:
        GitHubContentsClient(CONFIG).upload(b"DB", "old", "m")
    assert not isinstance(info.value, BackupConflict)


# ---------------------------------------------------------------------------
# 画面への組み込み
# ---------------------------------------------------------------------------


def run_app(secrets=None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=45)
    if secrets:
        for key, value in secrets.items():
            at.secrets[key] = value
    at.run()
    assert not at.exception
    return at


def test_app_without_settings_shows_nothing(remote, db_file):
    at = run_app()
    assert not any("控えて" in c.value for c in at.sidebar.caption)
    assert not at.error
    assert remote.downloads == 0


def test_app_restores_before_any_page_and_sends_changes(remote, db_file, tmp_path):
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    at = run_app(SECRETS)

    assert staff_names(db_file) == ["山田"]
    assert any("自動で保存先に控えています" in c.value for c in at.sidebar.caption)
    assert remote.uploads == []  # 開いただけでは送らない

    at.run()  # 何も変えずに再実行しても送らない
    assert remote.uploads == []

    next(b for b in at.button if b.label == "勤務表をつくる").click().run()
    next(b for b in at.button if b.label == "この期間で始める").click().run()
    assert not at.exception
    assert len(remote.uploads) == 1  # 期間を始めた（保存した）ので控えを送った
    assert any("最終" in c.value for c in at.sidebar.caption)


def test_app_warns_when_the_backup_cannot_be_read(remote, db_file, tmp_path):
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    remote.fail_download = True
    at = run_app(SECRETS)
    assert any(RESTORE_FAILED_MESSAGE == e.value for e in at.error)
    assert remote.uploads == []


def test_app_does_not_open_an_empty_database_while_the_backup_cannot_be_read(
    remote, db_file, tmp_path
):
    """読み込めていないあいだは空の画面（スタッフ0人）を出さず、「もう一度読み込む」だけを出す."""
    remote.put(make_db(tmp_path / "saved.db", "山田"))
    remote.fail_download = True
    at = run_app(SECRETS)
    assert not db_file.exists()  # 空のデータベースを作らない
    assert not [b for b in at.button if b.label == "勤務表をつくる"]

    remote.fail_download = False
    next(b for b in at.button if b.label == "もう一度読み込む").click().run()
    assert not at.exception
    assert staff_names(db_file) == ["山田"]
    assert [b for b in at.button if b.label == "勤務表をつくる"]  # ホーム画面に戻る
    assert any("自動で保存先に控えています" in c.value for c in at.sidebar.caption)
