"""services: 旧生成フローに依存しないこと."""

import sys
from pathlib import Path

import pytest

from src import services
from src.database import get_connection, initialize_database

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


def test_get_role_names(conn):
    assert services.get_role_names(conn) == {1: "リーダー", 2: "チェッカー", 3: "クリーナー"}


def test_generation_flow_removed():
    """生成結果をDBへ保存する旧フローは持たない（Phase 8では永続化しない）."""
    for name in ("generate_and_save", "run_precheck_for_month", "confirm_month", "apply_manual_edit"):
        assert not hasattr(services, name), name


def test_old_solver_modules_not_reintroduced():
    """Phase 8でOR-Toolsを再導入したが、元アプリのモジュールは復活させない.

    現在のデータモデルに合わせた src/shift_generation.py を新規に作っている。
    """
    for name in ("src.scheduler", "src.precheck", "src.availability", "src.schedule_validation"):
        assert name not in sys.modules, name
    assert not (REPO_ROOT / "src" / "scheduler.py").exists()


def test_generation_entry_point_exists():
    assert hasattr(services, "generate_schedule")
    assert hasattr(services, "build_generation_request")


def test_solver_does_not_depend_on_db_or_streamlit():
    """Solver本体はSQLite・Streamlitへ直接依存しないこと（Service層が仲介する）."""
    source = (REPO_ROOT / "src" / "shift_generation.py").read_text(encoding="utf-8")
    for forbidden in ("sqlite3", "streamlit", "src.repositories", "src.database"):
        assert forbidden not in source, forbidden
