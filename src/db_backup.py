"""データベースを GitHub の非公開リポジトリへ自動で控える（Streamlit Cloud 用）.

Streamlit Community Cloud は、アプリが再起動するとサーバー内のファイルを残さない。
data/app.db をそのまま置くと、再起動・スリープ・マージのたびに登録内容が消える。そこで

- 起動直後、手元にデータベースが無ければ、非公開リポジトリの控えから戻す
- 画面の処理が終わるたびに、内容が変わっていれば非公開リポジトリへ控えを送る

設定（Streamlit の Secrets の [data_backup]）が無いときは何もしない。
PC（start.cmd）での利用はこれまでどおり data/app.db だけを使う。

控えの読み込みに失敗したときは、空のデータで控えを上書きしないよう、
そのプロセスでは控えを送らない（画面に知らせる）。

Secrets の例::

    [data_backup]
    repo = "highwing-republic/cleaning-staff-data"
    token = "github_pat_..."     # そのリポジトリの Contents だけ読み書きできるトークン
    # branch = "main"            # 省略時はリポジトリの既定ブランチ
    # path = "app.db"            # 省略時は app.db
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

SECRETS_SECTION = "data_backup"
DEFAULT_REMOTE_PATH = "app.db"
API_ROOT = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 20
JST = timezone(timedelta(hours=9))
# 控えを読めなかったとき、次に読み直すまでの秒数（「もう一度読み込む」ボタンなら待たずに読み直す）
RESTORE_RETRY_SECONDS = 10
# 起動直後は通信が不安定なことがあるため、1回の画面表示の中で何回か読み直す
RESTORE_ATTEMPTS = 3
RESTORE_BACKOFF_SECONDS = (1.0, 2.0)

RESTORE_FAILED_MESSAGE = (
    "保存先（GitHub）からデータを読み込めませんでした。"
    "保存先のデータは無事ですが、読み込めるまで画面を止めています。"
    "少し待ってから「もう一度読み込む」を押してください。続くときは管理者に連絡してください。"
)
BACKUP_FAILED_MESSAGE = (
    "直前の変更を保存先（GitHub）へ控えられませんでした。"
    "この画面では保存されていますが、アプリが再起動すると消えるおそれがあります。"
    "もう一度保存し直すか、管理者に連絡してください。"
)


class BackupError(Exception):
    """控えの読み書きに失敗した（ネットワーク・権限・GitHub側のエラー）."""


class BackupConflict(BackupError):
    """控えが別の処理で更新されていた（sha が古い）."""


@dataclass(frozen=True)
class BackupConfig:
    repo: str
    token: str = field(repr=False)  # ログや画面に出さない
    branch: str | None = None
    path: str = DEFAULT_REMOTE_PATH


def config_from_mapping(section) -> BackupConfig | None:
    """Secrets の [data_backup] から設定を作る. repo と token が揃わなければ None."""
    if not section:
        return None
    repo = str(section.get("repo") or "").strip()
    token = str(section.get("token") or "").strip()
    if not repo or "/" not in repo or not token:
        return None
    branch = str(section.get("branch") or "").strip() or None
    path = str(section.get("path") or "").strip().lstrip("/") or DEFAULT_REMOTE_PATH
    return BackupConfig(repo=repo, token=token, branch=branch, path=path)


def load_config() -> BackupConfig | None:
    """Streamlit の Secrets から設定を読む. Secrets が無い環境（PC・テスト）では None."""
    try:
        import streamlit as st

        section = st.secrets.get(SECRETS_SECTION)
    except Exception:  # Secrets ファイルが無いと例外になる
        return None
    return config_from_mapping(section)


def _log(message: str) -> None:
    """Streamlit Cloud の「Manage app」のログに出す（トークンは渡さないこと）."""
    print(f"data backup: {message}", file=sys.stderr, flush=True)


_SSL_CONTEXT: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext:
    """OS の証明書に certifi の証明書を足した SSL 設定.

    Streamlit Cloud は作り直しのたびに Python の環境が変わることがあり、
    その Python が OS の証明書を見つけられないと GitHub に接続できない。certifi があれば足しておく。
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        context = ssl.create_default_context()
        try:
            import certifi

            context.load_verify_locations(cafile=certifi.where())
        except Exception:  # certifi が無い環境では OS の証明書だけを使う
            pass
        _SSL_CONTEXT = context
    return _SSL_CONTEXT


class GitHubContentsClient:
    """GitHub の Contents API で1ファイルを読み書きする（標準ライブラリだけで実装）."""

    def __init__(self, config: BackupConfig, timeout: float = REQUEST_TIMEOUT_SECONDS):
        self.config = config
        self.timeout = timeout

    def _url(self) -> str:
        path = urllib.parse.quote(self.config.path)
        url = f"{API_ROOT}/repos/{self.config.repo}/contents/{path}"
        if self.config.branch:
            url += "?ref=" + urllib.parse.quote(self.config.branch)
        return url

    def _request(self, method: str, url: str, *, body: dict | None = None,
                 accept: str = "application/vnd.github+json") -> tuple[int, bytes]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.config.token}")
        request.add_header("Accept", accept)
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "cleaning-staff-app-backup")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=_ssl_context()
            ) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read() or b""
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            # 原因（証明書・名前解決など）が分かるよう理由も残す. 念のためトークンは伏せる
            reason = getattr(exc, "reason", exc)
            detail = f"{type(exc).__name__}: {reason}".replace(self.config.token, "***")
            raise BackupError(f"network error: {detail[:300]}") from None

    def download(self) -> tuple[bytes | None, str | None]:
        """(ファイルの中身, sha). 控えがまだ無ければ (None, None)."""
        status, payload = self._request("GET", self._url())
        if status == 404:
            # 非公開リポジトリは、名前の誤りやトークンの権限不足でも 404 になる。
            # 「控えがまだ無い」と取り違えないよう、リポジトリ自体に届くか確かめる
            repo_status, _ = self._request("GET", f"{API_ROOT}/repos/{self.config.repo}")
            if repo_status != 200:
                raise BackupError(f"repository not reachable (GitHub returned {repo_status})")
            return None, None
        if status != 200:
            raise BackupError(f"GitHub returned {status} on download")
        meta = json.loads(payload)
        sha = meta.get("sha")
        if meta.get("encoding") == "base64" and meta.get("content"):
            return base64.b64decode(meta["content"]), sha
        # 1MB を超えるファイルは中身が入らないので、生データを取り直す
        status, raw = self._request("GET", self._url(), accept="application/vnd.github.raw")
        if status != 200:
            raise BackupError(f"GitHub returned {status} on raw download")
        return raw, sha

    def upload(self, data: bytes, sha: str | None, message: str) -> str:
        """控えを作成・更新し、新しい sha を返す."""
        body: dict = {"message": message, "content": base64.b64encode(data).decode("ascii")}
        if sha:
            body["sha"] = sha
        if self.config.branch:
            body["branch"] = self.config.branch
        url = f"{API_ROOT}/repos/{self.config.repo}/contents/{urllib.parse.quote(self.config.path)}"
        status, payload = self._request("PUT", url, body=body)
        if status in (409, 422):
            raise BackupConflict(f"GitHub returned {status} on upload")
        if status not in (200, 201):
            raise BackupError(f"GitHub returned {status} on upload")
        return json.loads(payload)["content"]["sha"]


@dataclass
class _State:
    enabled: bool = False
    config: BackupConfig | None = None
    # プロセスの起動時（最初の画面表示）に手元にデータベースがあったか. None はまだ調べていない
    local_existed_at_start: bool | None = None
    next_retry_at: datetime | None = None
    logged_no_config: bool = False
    # 控えにスタッフが登録されているか（空のデータベースで上書きしないための目印）
    remote_has_staff: bool = False
    remote_sha: str | None = None
    last_hash: str | None = None
    last_saved_at: datetime | None = None
    error: str | None = None


_STATE = _State()
_LOCK = threading.Lock()
# テストでは偽物のクライアントに差し替える
client_factory = GitHubContentsClient


def reset_state() -> None:
    """テスト用: プロセス内の状態を初期化する."""
    global _STATE
    with _LOCK:
        _STATE = _State()


def db_path() -> Path:
    """アプリが使うデータベースのパス（open_connection と同じ決め方）."""
    from src.database import DEFAULT_DB_PATH
    from src.ui_common import DB_PATH_ENV

    configured = os.environ.get(DB_PATH_ENV)
    return Path(configured) if configured else DEFAULT_DB_PATH


def _copy_database(path: Path, target: Path) -> None:
    """書き込み途中の状態を避けるため、SQLite のバックアップ機能で写しを取る."""
    source = sqlite3.connect(str(path))
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def snapshot_bytes(path: Path) -> bytes:
    """データベースの写しの中身."""
    with tempfile.TemporaryDirectory() as tmp:
        copy_path = Path(tmp) / "snapshot.db"
        _copy_database(path, copy_path)
        return copy_path.read_bytes()


def content_digest(path: Path) -> str:
    """登録内容の指紋. 内容が同じなら同じ値になる.

    ファイルの中身をそのまま比べると、画面を開くだけで変わる値
    （初期データ投入のたびに進む sqlite_sequence の連番）でも「変わった」と判断し、
    操作のたびに控えを送ってしまう。そこで SQL の書き出しから連番の行を除いて比べる。
    """
    connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    digest = hashlib.sha256()
    try:
        for line in connection.iterdump():
            if '"sqlite_sequence"' in line:
                continue
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
    finally:
        connection.close()
    return digest.hexdigest()


def has_staff(path: Path) -> bool:
    """スタッフが1人でも登録されているか. スタッフは削除せず無効化するだけなので、
    0人のデータベースは「作られたばかりの空のもの」とみなせる."""
    connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        return connection.execute("SELECT EXISTS (SELECT 1 FROM staff)").fetchone()[0] == 1
    except sqlite3.Error:
        return False  # 表がまだ無い
    finally:
        connection.close()


def _describe(exc: Exception) -> str:
    """ログ用. BackupError は状態コードだけを持つ. それ以外は種類だけ（トークンを書き出さないため）."""
    return str(exc) if isinstance(exc, BackupError) else type(exc).__name__


def _now() -> datetime:
    return datetime.now(JST)


def _download_with_retries(client) -> tuple[bytes | None, str | None]:
    """控えを読む. 起動直後の不安定な通信に備え、少し待って何回か読み直す."""
    for attempt in range(1, RESTORE_ATTEMPTS + 1):
        try:
            return client.download()
        except Exception as exc:
            _log(f"restore failed (attempt {attempt}/{RESTORE_ATTEMPTS}): {_describe(exc)}")
            if attempt == RESTORE_ATTEMPTS:
                raise
            time.sleep(RESTORE_BACKOFF_SECONDS[min(attempt, len(RESTORE_BACKOFF_SECONDS)) - 1])
    raise AssertionError("unreachable")


def ensure_restored(config: BackupConfig | None = None) -> None:
    """画面を描く前に毎回呼ぶ. 起動時に手元にデータベースが無かったら控えから戻す.

    - 控えを読めたら、以後は内容が変わるたびに控えを送る（backup_if_changed）
    - 起動時に手元にあったデータベースは控えで上書きしない
    - 控えを読めなかったときは控えの送信を止める（status()["restore_failed"]）。
      画面はデータを開かずに止め、10秒後の画面表示か retry_now() で読み直す
      （起動時に手元に無かった場合は、読めた時点で控えの内容に入れ替える）
    - 設定が無ければ何もしない. Secrets をあとから設定した場合は、次の画面表示から始める
      （その場合も、起動後に作られたデータベースは起動時に無かったものとして控えを優先する）
    データベースを開く前（どの画面よりも先）に呼ぶこと。
    """
    with _LOCK:
        if _STATE.enabled:
            return
        path = db_path()
        if _STATE.local_existed_at_start is None:
            _STATE.local_existed_at_start = path.exists()
        now = _now()
        if _STATE.next_retry_at is not None and now < _STATE.next_retry_at:
            return
        config = config or load_config()
        if config is None:
            if not _STATE.logged_no_config:
                _log("settings [data_backup] not found; backup is off")
                _STATE.logged_no_config = True
            return

        client = client_factory(config)
        try:
            data, sha = _download_with_retries(client)
            if data is None:
                _STATE.remote_has_staff = False
                _log(f"no backup file yet in {config.repo}; it will be created on the first change")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                partial = path.with_suffix(path.suffix + ".restoring")
                partial.write_bytes(data)
                try:
                    digest = content_digest(partial)  # 壊れた控えで手元を置き換えない
                    _STATE.remote_has_staff = has_staff(partial)
                except Exception:
                    partial.unlink(missing_ok=True)
                    raise
                # 手元のデータベースを残すのは、起動時からあり、スタッフが登録されているときだけ。
                # 控えを読めない間に作られた空のデータベースが残っていても、控えで置き換える
                keep_local = (
                    _STATE.local_existed_at_start and path.exists() and has_staff(path)
                )
                if keep_local:
                    partial.unlink(missing_ok=True)
                    _log("kept the database that was already on this server")
                else:
                    if path.exists():
                        _log("the database on this server has no staff; using the backup instead")
                    partial.replace(path)
                    _STATE.last_hash = digest
                    _log(f"restored {len(data)} bytes from {config.repo}")
        except Exception as exc:  # 読めなければ控えを送らない（空のデータで上書きしないため）
            _log(f"restore failed: {_describe(exc)}")
            _STATE.error = RESTORE_FAILED_MESSAGE
            _STATE.next_retry_at = now + timedelta(seconds=RESTORE_RETRY_SECONDS)
            return
        _STATE.remote_sha = sha
        _STATE.config = config
        _STATE.enabled = True
        _STATE.next_retry_at = None
        _STATE.error = None


def retry_now() -> None:
    """「もう一度読み込む」ボタン用: 待ち時間を待たずに、次の ensure_restored で読み直す."""
    with _LOCK:
        if not _STATE.enabled:
            _STATE.next_retry_at = None


def backup_if_changed(config: BackupConfig | None = None) -> bool:
    """内容が変わっていれば控えを送る. 送ったら True."""
    if not _STATE.enabled:
        return False
    config = config or _STATE.config
    if config is None:
        return False
    path = db_path()
    if not path.exists():
        return False

    with _LOCK:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                copy_path = Path(tmp) / "snapshot.db"
                _copy_database(path, copy_path)
                digest = content_digest(copy_path)
                if digest == _STATE.last_hash:
                    return False
                if _STATE.remote_has_staff and not has_staff(copy_path):
                    # スタッフのいる控えを、スタッフ0人のデータベースで上書きしない（最後の安全装置）
                    _log("refused to replace a backup that has staff with a database that has none")
                    _STATE.error = BACKUP_FAILED_MESSAGE
                    return False
                local_has_staff = has_staff(copy_path)
                data = copy_path.read_bytes()
            now = _now()
            message = f"backup: {config.path} ({now:%Y-%m-%d %H:%M} JST)"
            client = client_factory(config)
            try:
                sha = client.upload(data, _STATE.remote_sha, message)
            except BackupConflict:
                # 別の画面が先に控えを更新していた: 最新の sha を取り直して1回だけやり直す
                _, latest_sha = client.download()
                sha = client.upload(data, latest_sha, message)
        except Exception as exc:  # 画面の処理（再実行など）を止めないよう、ここで受け止めて知らせる
            _log(f"upload failed: {_describe(exc)}")
            _STATE.error = BACKUP_FAILED_MESSAGE
            return False
        _STATE.remote_sha = sha
        _STATE.last_hash = digest
        _STATE.last_saved_at = now
        _STATE.remote_has_staff = local_has_staff
        _STATE.error = None
        return True


def status() -> dict:
    """画面表示用の状態. restore_failed は控えを読めずに送信を止めている状態."""
    return {
        "enabled": _STATE.enabled,
        "error": _STATE.error,
        "restore_failed": _STATE.error == RESTORE_FAILED_MESSAGE,
        "last_saved_at": _STATE.last_saved_at,
    }
