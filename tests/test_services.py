"""services: 旧生成フローに依存しないこと."""

import sys

import pytest

from src import services
from src.database import get_connection, initialize_database


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


def test_get_role_names(conn):
    assert services.get_role_names(conn) == {1: "リーダー", 2: "チェッカー", 3: "クリーナー"}


def test_generation_flow_removed():
    for name in ("generate_and_save", "run_precheck_for_month", "confirm_month", "apply_manual_edit"):
        assert not hasattr(services, name), name


def test_no_solver_modules_loaded():
    assert "ortools" not in sys.modules
    for name in ("src.scheduler", "src.precheck", "src.availability", "src.schedule_validation"):
        assert name not in sys.modules, name
