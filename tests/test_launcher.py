"""Launcher checks that never start a real Streamlit server."""

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "start_app.py"
SPEC = importlib.util.spec_from_file_location("start_app", MODULE_PATH)
start_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(start_app)


@pytest.fixture(autouse=True)
def prevent_browser_and_network(monkeypatch):
    monkeypatch.setattr(start_app.threading.Thread, "start", lambda self: None)


def test_starts_streamlit_on_localhost(monkeypatch):
    calls = []
    monkeypatch.setattr(start_app.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(start_app, "port_is_available", lambda host, port: True)
    monkeypatch.setattr(start_app.subprocess, "call", lambda *args, **kwargs: calls.append((args, kwargs)) or 0)

    assert start_app.main() == 0

    args, kwargs = calls[0]
    command = args[0]
    assert command[:3] == [sys.executable, "-m", "streamlit"]
    assert command[3:5] == ["run", str(start_app.PROJECT_DIR / "app.py")]
    assert f"--server.address={start_app.HOST}" in command
    assert f"--server.port={start_app.PORT}" in command
    assert "--server.headless=true" in command
    assert "--browser.gatherUsageStats=false" in command
    assert kwargs == {"cwd": start_app.PROJECT_DIR, "shell": False}


def test_missing_streamlit_fails_without_launch(monkeypatch, capsys):
    monkeypatch.setattr(start_app.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(start_app.subprocess, "call", lambda *args, **kwargs: pytest.fail("must not launch"))

    assert start_app.main() == 1
    assert "Streamlit" in capsys.readouterr().err


def test_occupied_port_fails_with_actionable_message(monkeypatch, capsys):
    monkeypatch.setattr(start_app.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(start_app, "port_is_available", lambda host, port: False)
    monkeypatch.setattr(start_app.subprocess, "call", lambda *args, **kwargs: pytest.fail("must not launch"))

    assert start_app.main() == 1
    message = capsys.readouterr().err
    assert str(start_app.PORT) in message
    assert "すべて使用中" in message
    assert "管理者" in message


def test_server_exit_code_is_returned(monkeypatch):
    monkeypatch.setattr(start_app.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(start_app, "port_is_available", lambda host, port: True)
    monkeypatch.setattr(start_app.subprocess, "call", lambda *args, **kwargs: 7)

    assert start_app.main() == 7


def test_uses_next_available_port_without_stopping_other_apps(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(start_app.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(start_app, "port_is_available", lambda host, port: port == 8503)
    monkeypatch.setattr(start_app.subprocess, "call", lambda *args, **kwargs: calls.append(args[0]) or 0)

    assert start_app.main() == 0
    assert "--server.port=8503" in calls[0]
    assert "http://127.0.0.1:8503" in capsys.readouterr().out


def test_browser_opens_only_after_server_is_healthy(monkeypatch):
    from unittest.mock import MagicMock
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = 200
    response.read.return_value = b"ok"
    opener = MagicMock()
    opener.open.return_value = response
    monkeypatch.setattr(start_app.urllib.request, "build_opener", lambda *args: opener)
    opened = []
    monkeypatch.setattr(start_app.webbrowser, "open", lambda url: opened.append(url))
    start_app.open_browser_when_ready(8503, start_app.threading.Event())
    assert opened == ["http://127.0.0.1:8503"]
    opener.open.assert_called_once_with("http://127.0.0.1:8503/_stcore/health", timeout=1)


def test_browser_not_opened_if_startup_failed(monkeypatch):
    stopped = start_app.threading.Event()
    stopped.set()
    monkeypatch.setattr(start_app.webbrowser, "open", lambda url: pytest.fail("must not open"))
    start_app.open_browser_when_ready(8503, stopped)


def test_windows_launcher_uses_crlf_line_endings():
    data = (MODULE_PATH.parent / "start.cmd").read_bytes()
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")
