"""Start the local Streamlit application using this project's Python."""

from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8501
PORT_ATTEMPTS = 10


def open_browser_when_ready(port: int, stopped: threading.Event) -> None:
    """Open only after startup; headless server startup avoids email prompts."""
    url = f"http://{HOST}:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(120):
        if stopped.is_set():
            return
        try:
            with opener.open(url + "/_stcore/health", timeout=1) as response:
                ready = response.status == 200 and response.read(32).strip() == b"ok"
            if ready:
                webbrowser.open(url)
                return
        except (OSError, urllib.error.URLError):
            pass
        if stopped.wait(0.5):
            return


def port_is_available(host: str = HOST, port: int = PORT) -> bool:
    """Return whether the requested local port can be bound right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def main() -> int:
    if importlib.util.find_spec("streamlit") is None:
        print(
            "Streamlit が見つかりません。管理者に .venv の再セットアップを依頼してください。",
            file=sys.stderr,
        )
        return 1

    port = next((candidate for candidate in range(PORT, PORT + PORT_ATTEMPTS)
                 if port_is_available(HOST, candidate)), None)
    if port is None:
        print(
            f"起動に使う番号 {PORT}～{PORT + PORT_ATTEMPTS - 1} がすべて使用中です。"
            "管理者にお問い合わせください。ほかのアプリは停止していません。",
            file=sys.stderr,
        )
        return 1

    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(PROJECT_DIR / "app.py"),
        f"--server.address={HOST}",
        f"--server.port={port}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    print(f"勤務表アプリを起動します。ブラウザが開かない場合はこちら: http://{HOST}:{port}", flush=True)
    stopped = threading.Event()
    threading.Thread(target=open_browser_when_ready, args=(port, stopped), daemon=True).start()
    try:
        return subprocess.call(command, cwd=PROJECT_DIR, shell=False)
    except OSError as exc:
        print(f"アプリを起動できませんでした: {exc}", file=sys.stderr)
        return 1
    finally:
        stopped.set()


if __name__ == "__main__":
    raise SystemExit(main())
