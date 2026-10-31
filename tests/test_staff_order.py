"""スタッフの並び順（スタッフ管理で上下に並べ替え. 勤務表・印刷・Excel もこの順）のテスト."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database
from src.period_utils import period_dates
from src.ui_common import DB_PATH_ENV

LEADER, CHECKER, CLEANER = 1, 2, 3


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "order.sqlite"
    monkeypatch.setenv(DB_PATH_ENV, str(path))
    connection = get_connection(path)
    initialize_database(connection)
    yield connection
    connection.close()


def add(conn, code, role_id=CLEANER, name=None):
    return repo.create_staff(conn, code, name or f"S{code}", role_id, 3, standard_start_time="09:00",
                             standard_end_time="15:30", weekdays=list(range(7)))


def names(conn, include_inactive=True):
    return [s.staff_name for s in repo.list_staff(conn, include_inactive=include_inactive)]


def test_existing_staff_get_the_old_print_order(conn):
    """並び順の列を足したDBでは、それまでの印刷と同じ「役割 → 従業員番号」の順にする."""
    with conn:
        for code, role in (("30", CLEANER), ("10", CLEANER), ("20", LEADER), ("05", CHECKER)):
            conn.execute("INSERT INTO staff (employee_code, staff_name, role_id) VALUES (?, ?, ?)",
                         (code, f"S{code}", role))
    initialize_database(conn)  # 起動時に並び順を付ける
    assert names(conn) == ["S20", "S05", "S10", "S30"]


def test_new_staff_go_after_the_same_role(conn):
    add(conn, "1", LEADER)
    add(conn, "2", CLEANER)
    add(conn, "3", CHECKER)  # チェッカーはまだいないので最後
    add(conn, "4", LEADER)  # リーダーのすぐ後ろ
    assert names(conn) == ["S1", "S4", "S2", "S3"]


def test_move_up_and_down(conn):
    ids = [add(conn, str(i)) for i in range(1, 5)]
    assert repo.move_staff(conn, ids[3], -1)
    assert names(conn) == ["S1", "S2", "S4", "S3"]
    assert repo.move_staff(conn, ids[0], 1)
    assert names(conn) == ["S2", "S1", "S4", "S3"]
    assert not repo.move_staff(conn, ids[1], -1)  # いちばん上
    assert not repo.move_staff(conn, ids[2], 1)  # いちばん下
    assert names(conn) == ["S2", "S1", "S4", "S3"]


def test_inactive_staff_keep_their_place(conn):
    ids = [add(conn, str(i)) for i in range(1, 4)]
    repo.deactivate_staff(conn, ids[1])
    assert names(conn, include_inactive=False) == ["S1", "S3"]
    assert names(conn) == ["S1", "S2", "S3"]


def test_reorder_requires_everyone_once(conn):
    ids = [add(conn, str(i)) for i in range(1, 4)]
    repo.reorder_staff(conn, [ids[2], ids[0], ids[1]])
    assert names(conn) == ["S3", "S1", "S2"]
    with pytest.raises(ValueError):
        repo.reorder_staff(conn, [ids[0], ids[1]])
    with pytest.raises(ValueError):
        repo.reorder_staff(conn, [ids[0], ids[0], ids[1]])


def test_schedule_export_and_generation_follow_the_order(conn):
    leader = add(conn, "1", LEADER)
    cleaner = add(conn, "2", CLEANER)
    repo.move_staff(conn, cleaner, -1)  # クリーナーを先頭へ
    dates = period_dates("2026-11-02", 3)
    assert [s.staff_id for s in services.build_regeneration_request(conn, dates).staff] == [cleaner, leader]
    assert [r.staff.staff_id for r in services.build_schedule_export(conn, dates).staff_rows] == [cleaner, leader]


# ---------------------------------------------------------------------------
# スタッフ管理の画面
# ---------------------------------------------------------------------------

STAFF_PAGE = str(Path(__file__).resolve().parents[1] / "pages" / "01_staff.py")


def test_staff_page_moves_a_row_with_the_buttons(conn):
    ids = [add(conn, str(i)) for i in range(1, 4)]
    at = AppTest.from_file(STAFF_PAGE, default_timeout=30).run()
    assert at.button(key=f"order_up_{ids[0]}").disabled
    assert at.button(key=f"order_down_{ids[2]}").disabled
    at.button(key=f"order_up_{ids[2]}").click().run()
    assert not at.exception
    assert names(conn) == ["S1", "S3", "S2"]
    assert list(at.dataframe[0].value["氏名"]) == ["S1", "S3", "S2"]
    at.button(key=f"order_down_{ids[0]}").click().run()
    assert names(conn) == ["S3", "S1", "S2"]
