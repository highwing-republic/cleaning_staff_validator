"""スタッフマスター（通常勤務条件・特殊スキル）のservice層テスト."""

import pytest

from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database
from src.validation import (
    STAFF_SPECIAL_SKILL_DUPLICATED,
    STAFF_SPECIAL_SKILL_NOT_FOUND,
    STAFF_STANDARD_TIME_ORDER_INVALID,
)

CLEANER = 3


@pytest.fixture()
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


@pytest.fixture()
def heavy_work_id(conn):
    return repo.list_special_skills(conn)[0].special_skill_id


def _codes(errors):
    return [e.code for e in errors]


def _add_skill(conn, code, name, active=1):
    with conn:
        cur = conn.execute(
            "INSERT INTO special_skills (skill_code, skill_name, active) VALUES (?, ?, ?)",
            (code, name, active),
        )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# 特殊スキルの選択肢
# ---------------------------------------------------------------------------


def test_options_include_seeded_skill(conn):
    options = services.list_special_skill_options(conn)
    assert [s.skill_name for s in options] == ["力仕事可"]


def test_options_follow_display_order(conn):
    _add_skill(conn, "TRAIN_NEW", "新人指導")  # display_order=0 なので先に並ぶ
    assert [s.skill_name for s in services.list_special_skill_options(conn)] == [
        "新人指導",
        "力仕事可",
    ]


def test_inactive_skill_is_not_offered_for_new_staff(conn):
    _add_skill(conn, "OLD", "廃止スキル", active=0)
    names = [s.skill_name for s in services.list_special_skill_options(conn)]
    assert "廃止スキル" not in names


def test_inactive_skill_is_kept_for_staff_already_assigned(conn):
    old_id = _add_skill(conn, "OLD", "廃止スキル", active=0)
    names = [s.skill_name for s in services.list_special_skill_options(conn, [old_id])]
    assert "廃止スキル" in names


def test_get_special_skill_names(conn, heavy_work_id):
    assert services.get_special_skill_names(conn)[heavy_work_id] == "力仕事可"


# ---------------------------------------------------------------------------
# 入力検証（DBが必要な存在確認を含む）
# ---------------------------------------------------------------------------


def test_valid_input_has_no_errors(conn, heavy_work_id):
    errors = services.validate_staff_master_input(
        conn, "0007", "Aさん", 4,
        standard_start_time="09:00",
        standard_end_time="15:30",
        target_days_per_week=4,
        max_consecutive_days=5,
        weekdays=[0, 1, 3, 4, 5],
        special_skill_ids=[heavy_work_id],
    )
    assert errors == []


def test_unknown_special_skill_is_rejected(conn):
    errors = services.validate_staff_master_input(
        conn, "0007", "Aさん", 4, special_skill_ids=[999]
    )
    assert STAFF_SPECIAL_SKILL_NOT_FOUND in _codes(errors)


def test_inactive_special_skill_id_is_still_a_known_skill(conn):
    """無効な特殊スキルは選択肢に出さないが、既存割当の保存は妨げない."""
    old_id = _add_skill(conn, "OLD", "廃止スキル", active=0)
    errors = services.validate_staff_master_input(
        conn, "0007", "Aさん", 4, special_skill_ids=[old_id]
    )
    assert errors == []


def test_duplicate_special_skill_is_rejected(conn, heavy_work_id):
    errors = services.validate_staff_master_input(
        conn, "0007", "Aさん", 4, special_skill_ids=[heavy_work_id, heavy_work_id]
    )
    assert STAFF_SPECIAL_SKILL_DUPLICATED in _codes(errors)


def test_base_validation_is_applied(conn):
    errors = services.validate_staff_master_input(
        conn, "0007", "Aさん", 4, standard_start_time="15:00", standard_end_time="09:00"
    )
    assert STAFF_STANDARD_TIME_ORDER_INVALID in _codes(errors)


def test_empty_special_skills_skip_existence_check(conn):
    assert services.validate_staff_master_input(conn, "0007", "Aさん", 4, special_skill_ids=[]) == []


# ---------------------------------------------------------------------------
# 検証 → 保存の流れ（画面が通る経路）
# ---------------------------------------------------------------------------


def test_validated_input_can_be_saved_and_reloaded(conn, heavy_work_id):
    kwargs = dict(
        standard_start_time="09:00",
        standard_end_time="13:00",
        target_days_per_week=3,
        max_days_per_period=8,
        max_consecutive_days=4,
        weekdays=[0, 1, 2, 3, 4],
        special_skill_ids=[heavy_work_id],
    )
    assert services.validate_staff_master_input(conn, "0008", "Bさん", 2, **kwargs) == []

    staff_id = repo.create_staff(conn, "0008", "Bさん", CLEANER, 2, "清掃", **kwargs)
    detail = repo.get_staff_detail(conn, staff_id)
    assert detail.staff.standard_end_time == "13:00"
    assert detail.staff.target_days_per_week == 3
    assert detail.staff.max_days_per_period == 8
    assert detail.staff.max_consecutive_days == 4
    assert detail.weekdays == (0, 1, 2, 3, 4)
    assert detail.special_skill_ids == (heavy_work_id,)


def test_attendance_matching_still_works_with_new_columns(conn):
    """既存の勤怠CSV照合（employee_codeでの突合）が通常勤務条件の追加後も動くこと."""
    repo.create_staff(
        conn, "0015", "テスト清掃A", CLEANER, 4, "清掃",
        standard_start_time="09:00", standard_end_time="15:30", weekdays=[0, 1],
    )
    staff = repo.get_staff_by_employee_code(conn, "0015")
    assert staff.staff_name == "テスト清掃A"
    assert staff.standard_start_time == "09:00"
