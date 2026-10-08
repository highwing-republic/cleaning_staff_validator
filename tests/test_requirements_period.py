"""予約・必要人数（期間単位）のDB制約・repository・serviceのテスト.

指示書のD01〜D11に対応するテストには対応番号をコメントで示す。
"""

import sqlite3

import pytest

from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database
from src.models import DailyRequirementInput, RoleRequirementInput
from src.period_utils import period_dates
from src.requirement_display import (
    REQUIREMENT_UNDEFINED_LABEL,
    count_defined,
    count_undefined,
    format_max_staff,
    format_requirement_summary,
    format_reserved_rooms,
    format_required_staff,
    format_role_condition,
    format_skill_condition,
    total_reserved_rooms,
)
from src.validation import (
    REQUIREMENT_DATE_OUT_OF_PERIOD,
    REQUIREMENT_DUPLICATED_DATE,
    REQUIREMENT_REQUIRED_EXCEEDS_MAX,
    REQUIREMENT_RESERVED_ROOMS_INVALID,
    REQUIREMENT_ROLE_NOT_FOUND,
    REQUIREMENT_SKILL_LEVEL_REQUIRED,
)

LEADER, CHECKER, CLEANER = 1, 2, 3
# 2026-10-20 〜 2026-11-02（月をまたぐ14日間）
DATES = period_dates("2026-10-20", 14)
FIRST, LAST = DATES[0], DATES[-1]


@pytest.fixture()
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


def req(work_date, required_total_staff=3, **kwargs) -> DailyRequirementInput:
    return DailyRequirementInput(
        work_date=work_date, required_total_staff=required_total_staff, **kwargs
    )


# ---------------------------------------------------------------------------
# スキーマ（reserved_rooms）
# ---------------------------------------------------------------------------


def _insert_reserved_rooms(conn, value):
    with conn:
        conn.execute(
            "INSERT INTO daily_requirements (work_date, required_total_staff, reserved_rooms) "
            "VALUES (?, 4, ?)",
            (FIRST, value),
        )


@pytest.mark.parametrize("value", [None, 0, 1, 10, 999])
def test_reserved_rooms_accepts_null_and_non_negative(conn, value):
    _insert_reserved_rooms(conn, value)
    assert repo.get_daily_requirement(conn, FIRST).reserved_rooms == value


@pytest.mark.parametrize("value", [-1, -10])
def test_reserved_rooms_rejects_negative(conn, value):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_reserved_rooms(conn, value)


def test_reserved_rooms_defaults_to_null(conn):
    with conn:
        conn.execute(
            "INSERT INTO daily_requirements (work_date, required_total_staff) VALUES (?, 4)",
            (FIRST,),
        )
    assert repo.get_daily_requirement(conn, FIRST).reserved_rooms is None


def test_no_duplicate_demand_table_is_created(conn):
    """既存 daily_requirements を拡張して使う（daily_demand 等を重複して作らない）."""
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "daily_demand" not in tables
    assert "work_periods" not in tables
    assert "daily_requirements" in tables


# ---------------------------------------------------------------------------
# D01 / D02: 要件未設定 と 必要人数0
# ---------------------------------------------------------------------------


def test_d01_missing_row_means_requirement_undefined(conn):
    """D01: daily_requirements に行がない → 要件未設定."""
    assert repo.get_daily_requirement(conn, FIRST) is None
    view = services.get_period_requirements(conn, DATES)[0]
    assert view.is_defined is False
    assert view.requirement is None
    assert format_required_staff(view.requirement) == REQUIREMENT_UNDEFINED_LABEL
    assert format_requirement_summary(view) == REQUIREMENT_UNDEFINED_LABEL


def test_d02_required_zero_is_a_defined_requirement(conn):
    """D02: required_total_staff=0 → 設定済み・必要人数0（要件未設定ではない）."""
    repo.save_period_requirements(conn, DATES, [req(FIRST, 0)], [])
    view = services.get_period_requirements(conn, DATES)[0]
    assert view.is_defined is True
    assert view.requirement.required_total_staff == 0
    assert format_required_staff(view.requirement) == "0名"


def test_undefined_and_zero_are_distinguishable_in_the_same_period(conn):
    repo.save_period_requirements(conn, DATES, [req(FIRST, 0)], [])
    views = services.get_period_requirements(conn, DATES)
    assert views[0].is_defined is True
    assert views[1].is_defined is False
    assert count_defined(views) == 1
    assert count_undefined(views) == len(DATES) - 1


# ---------------------------------------------------------------------------
# D03〜D08: 期間保存
# ---------------------------------------------------------------------------


def test_d03_saves_across_month_boundary(conn):
    """D03: 10/20〜11/2 を月跨ぎで正常保存できる."""
    assert FIRST == "2026-10-20"
    assert LAST == "2026-11-02"
    repo.save_period_requirements(
        conn, DATES, [req("2026-10-31", 4), req("2026-11-01", 5), req("2026-11-02", 6)], []
    )
    saved = repo.list_daily_requirements(conn, FIRST, LAST)
    assert [r.work_date for r in saved] == ["2026-10-31", "2026-11-01", "2026-11-02"]


def test_d04_only_configured_days_are_stored(conn):
    """D04: 期間内14日中10日を設定 → 10行だけ存在する."""
    repo.save_period_requirements(conn, DATES, [req(d, 3) for d in DATES[:10]], [])
    assert len(repo.list_daily_requirements(conn, FIRST, LAST)) == 10


def test_d05_turning_off_deletes_daily_requirement(conn):
    """D05: 設定済みの日をOFF → daily_requirements の行が削除される."""
    repo.save_period_requirements(conn, DATES, [req(FIRST, 4), req(DATES[1], 5)], [])
    repo.save_period_requirements(conn, DATES, [req(DATES[1], 5)], [])
    assert repo.get_daily_requirement(conn, FIRST) is None
    assert repo.get_daily_requirement(conn, DATES[1]) is not None


def test_d06_turning_off_deletes_role_requirements(conn):
    """D06: 同日の daily_role_requirements も削除される."""
    repo.save_period_requirements(
        conn, DATES, [req(FIRST, 4)], [RoleRequirementInput(FIRST, LEADER, 1)]
    )
    assert repo.list_role_requirements(conn, FIRST, FIRST)

    repo.save_period_requirements(conn, DATES, [], [])
    assert repo.list_role_requirements(conn, FIRST, FIRST) == []


def test_no_orphan_role_rows_remain(conn):
    repo.save_period_requirements(
        conn,
        DATES,
        [req(FIRST, 4), req(DATES[1], 4)],
        [RoleRequirementInput(FIRST, LEADER, 1), RoleRequirementInput(DATES[1], CHECKER, 2)],
    )
    repo.save_period_requirements(conn, DATES, [], [])
    orphans = conn.execute(
        "SELECT COUNT(*) FROM daily_role_requirements r "
        "WHERE NOT EXISTS (SELECT 1 FROM daily_requirements d WHERE d.work_date = r.work_date)"
    ).fetchone()[0]
    assert orphans == 0


def test_d07_data_outside_the_period_is_untouched(conn):
    """D07: 期間外の daily_requirements / daily_role_requirements は変更しない."""
    repo.save_daily_requirement(conn, req("2026-10-10", 9, reserved_rooms=3))
    repo.save_role_requirement(conn, RoleRequirementInput("2026-10-10", LEADER, 2))
    repo.save_daily_requirement(conn, req("2026-12-01", 7))

    repo.save_period_requirements(conn, DATES, [], [])

    before = repo.get_daily_requirement(conn, "2026-10-10")
    assert (before.required_total_staff, before.reserved_rooms) == (9, 3)
    assert repo.list_role_requirements(conn, "2026-10-10", "2026-10-10")
    assert repo.get_daily_requirement(conn, "2026-12-01") is not None


def test_d08_rolls_back_on_failure(conn):
    """D08: 途中でエラー → 全体rollback（部分保存にならない）."""
    repo.save_period_requirements(conn, DATES, [req(FIRST, 4, reserved_rooms=2)], [])
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_period_requirements(
            conn,
            DATES,
            [
                req(DATES[1], 5),
                req(DATES[2], 1, reserved_rooms=-5),   # DB制約違反
            ],
            [],
        )
    saved = repo.list_daily_requirements(conn, FIRST, LAST)
    assert [r.work_date for r in saved] == [FIRST]
    assert saved[0].reserved_rooms == 2


def test_role_requirements_are_replaced_per_day(conn):
    repo.save_period_requirements(
        conn,
        DATES,
        [req(FIRST, 4)],
        [RoleRequirementInput(FIRST, LEADER, 1), RoleRequirementInput(FIRST, CHECKER, 2)],
    )
    repo.save_period_requirements(
        conn, DATES, [req(FIRST, 4)], [RoleRequirementInput(FIRST, CLEANER, 3)]
    )
    counts = {r.role_id: r.required_count for r in repo.list_role_requirements(conn, FIRST, FIRST)}
    assert counts == {CLEANER: 3}


def test_save_period_rejects_date_out_of_period(conn):
    with pytest.raises(ValueError):
        repo.save_period_requirements(conn, DATES, [req("2026-12-01", 1)], [])


def test_save_period_rejects_role_date_out_of_period(conn):
    with pytest.raises(ValueError):
        repo.save_period_requirements(
            conn, DATES, [], [RoleRequirementInput("2026-12-01", LEADER, 1)]
        )


def test_delete_daily_requirement_removes_role_rows(conn):
    repo.save_daily_requirement(conn, req(FIRST, 4))
    repo.save_role_requirement(conn, RoleRequirementInput(FIRST, LEADER, 1))
    repo.delete_daily_requirement(conn, FIRST)
    assert repo.get_daily_requirement(conn, FIRST) is None
    assert repo.list_role_requirements(conn, FIRST, FIRST) == []


# ---------------------------------------------------------------------------
# D09〜D11: 予約室数と必要人数は独立
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "reserved_rooms", "required_total_staff"),
    [
        ("D09 予約8 / 必要4", 8, 4),
        ("D10 予約0 / 必要3", 0, 3),
        ("D11 予約20 / 必要1", 20, 1),
        ("予約未入力 / 必要3", None, 3),
        ("予約8 / 必要0", 8, 0),
    ],
)
def test_reserved_rooms_and_required_staff_are_independent(
    conn, label, reserved_rooms, required_total_staff
):
    """アプリは必要人数を推定しない（入力値をそのまま保存する）."""
    errors = services.save_period_requirements(
        conn,
        DATES,
        [req(FIRST, required_total_staff, reserved_rooms=reserved_rooms)],
        [],
    )
    assert errors == []
    saved = repo.get_daily_requirement(conn, FIRST)
    assert saved.reserved_rooms == reserved_rooms
    assert saved.required_total_staff == required_total_staff


def test_zero_rooms_is_not_corrected_to_zero_staff(conn):
    """予約0室でも必要人数を勝手に0へ補正しないこと."""
    services.save_period_requirements(conn, DATES, [req(FIRST, 3, reserved_rooms=0)], [])
    assert repo.get_daily_requirement(conn, FIRST).required_total_staff == 3


# ---------------------------------------------------------------------------
# Role（マスターから動的に扱う）
# ---------------------------------------------------------------------------


def test_role_requirements_work_for_roles_added_later(conn):
    """新しいRoleをDBへ追加してもservice側が固定コードに依存しないこと."""
    with conn:
        cur = conn.execute(
            "INSERT INTO roles (role_code, role_name) VALUES ('INSPECTOR', '点検担当')"
        )
    new_role_id = cur.lastrowid

    errors = services.save_period_requirements(
        conn, DATES, [req(FIRST, 4)], [RoleRequirementInput(FIRST, new_role_id, 2)]
    )
    assert errors == []
    view = services.get_period_requirements(conn, DATES)[0]
    assert view.role_counts[new_role_id] == 2
    assert format_role_condition(view, new_role_id) == "2名"


def test_unknown_role_is_rejected(conn):
    errors = services.save_period_requirements(
        conn, DATES, [req(FIRST, 4)], [RoleRequirementInput(FIRST, 999, 1)]
    )
    assert REQUIREMENT_ROLE_NOT_FOUND in [e.code for e in errors]
    assert repo.get_daily_requirement(conn, FIRST) is None


def test_role_count_zero_means_no_condition(conn):
    services.save_period_requirements(
        conn, DATES, [req(FIRST, 4)], [RoleRequirementInput(FIRST, LEADER, 0)]
    )
    view = services.get_period_requirements(conn, DATES)[0]
    assert view.role_counts[LEADER] == 0
    assert format_role_condition(view, LEADER) == "-"


def test_role_sum_may_exceed_required_total_staff(conn):
    """ロール別必要人数の合計が最低人数を超えてもエラーにしない（既存仕様）."""
    errors = services.save_period_requirements(
        conn,
        DATES,
        [req(FIRST, 2)],
        [
            RoleRequirementInput(FIRST, LEADER, 3),
            RoleRequirementInput(FIRST, CHECKER, 3),
        ],
    )
    assert errors == []


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


def test_skill_condition_round_trip(conn):
    services.save_period_requirements(
        conn, DATES, [req(FIRST, 5, required_skill_level=4, required_skill_count=2)], []
    )
    saved = repo.get_daily_requirement(conn, FIRST)
    assert (saved.required_skill_level, saved.required_skill_count) == (4, 2)
    assert format_skill_condition(saved) == "Lv4+ 2名"


def test_skill_condition_can_be_absent(conn):
    services.save_period_requirements(conn, DATES, [req(FIRST, 5)], [])
    saved = repo.get_daily_requirement(conn, FIRST)
    assert (saved.required_skill_level, saved.required_skill_count) == (None, 0)
    assert format_skill_condition(saved) == "-"


def test_skill_count_without_level_is_rejected(conn):
    errors = services.save_period_requirements(
        conn, DATES, [req(FIRST, 5, required_skill_count=2)], []
    )
    assert REQUIREMENT_SKILL_LEVEL_REQUIRED in [e.code for e in errors]
    assert repo.get_daily_requirement(conn, FIRST) is None


# ---------------------------------------------------------------------------
# service の検証
# ---------------------------------------------------------------------------


def test_service_rejects_negative_reserved_rooms_without_saving(conn):
    errors = services.save_period_requirements(
        conn, DATES, [req(FIRST, 4, reserved_rooms=-1), req(DATES[1], 3)], []
    )
    assert REQUIREMENT_RESERVED_ROOMS_INVALID in [e.code for e in errors]
    assert repo.list_daily_requirements(conn, FIRST, LAST) == []


def test_service_rejects_required_over_max(conn):
    errors = services.save_period_requirements(
        conn, DATES, [req(FIRST, 8, max_total_staff=3)], []
    )
    assert REQUIREMENT_REQUIRED_EXCEEDS_MAX in [e.code for e in errors]


def test_service_rejects_out_of_period_date(conn):
    errors = services.save_period_requirements(conn, DATES, [req("2026-12-01", 1)], [])
    assert REQUIREMENT_DATE_OUT_OF_PERIOD in [e.code for e in errors]


def test_service_rejects_duplicated_date(conn):
    errors = services.save_period_requirements(
        conn, DATES, [req(FIRST, 1), req(FIRST, 2)], []
    )
    assert REQUIREMENT_DUPLICATED_DATE in [e.code for e in errors]


def test_service_get_period_requirements_covers_every_day(conn):
    repo.save_period_requirements(conn, DATES, [req(FIRST, 4)], [])
    views = services.get_period_requirements(conn, DATES)
    assert [v.work_date for v in views] == DATES
    assert views[0].is_defined is True
    assert all(not v.is_defined for v in views[1:])


def test_service_get_period_requirements_with_empty_period(conn):
    assert services.get_period_requirements(conn, []) == []


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------


def test_reserved_rooms_display_distinguishes_zero_and_unknown():
    assert format_reserved_rooms(0) == "0室"
    assert format_reserved_rooms(8) == "8室"
    assert format_reserved_rooms(None) == "未入力"


def test_max_staff_display():
    assert format_max_staff(None) == "-"
    assert format_max_staff(req(FIRST, 3)) == "上限なし"
    assert format_max_staff(req(FIRST, 3, max_total_staff=6)) == "6名"


def test_requirement_summary(conn):
    repo.save_period_requirements(conn, DATES, [req(FIRST, 4, reserved_rooms=8)], [])
    view = services.get_period_requirements(conn, DATES)[0]
    assert format_requirement_summary(view) == "予約8室 / 必要4名"


def test_total_reserved_rooms_skips_unknown(conn):
    repo.save_period_requirements(
        conn,
        DATES,
        [
            req(FIRST, 4, reserved_rooms=8),
            req(DATES[1], 4, reserved_rooms=0),
            req(DATES[2], 4),            # 未入力は加算しない
            req(DATES[3], 4, reserved_rooms=15),
        ],
        [],
    )
    views = services.get_period_requirements(conn, DATES)
    assert total_reserved_rooms(views) == 23


# ---------------------------------------------------------------------------
# 既存機能との互換
# ---------------------------------------------------------------------------


def test_month_based_api_still_works(conn):
    """既存の月単位API（Phase 3検証・CSV取り込みが使う）が動くこと."""
    repo.save_daily_requirement(conn, req("2026-10-05", 4, reserved_rooms=7))
    repo.save_role_requirement(conn, RoleRequirementInput("2026-10-05", LEADER, 1))

    monthly = repo.get_daily_requirements(conn, "2026-10")
    assert [r.work_date for r in monthly] == ["2026-10-05"]
    assert monthly[0].reserved_rooms == 7
    assert [r.role_id for r in repo.get_role_requirements(conn, "2026-10")] == [LEADER]


def test_reserved_rooms_is_not_used_by_staffing_validation(conn):
    """reserved_rooms は検証判定に使わない（既存の判定結果を変えない）."""
    from src.staffing_validation import validate_month_staffing

    repo.create_staff(conn, "0001", "Aさん", CLEANER, 3, "清掃")
    requirement = req("2026-10-05", 2)
    with_rooms = req("2026-10-05", 2, reserved_rooms=20)

    role_names = {r["role_id"]: r["role_name"] for r in repo.list_roles(conn)}
    staff = repo.list_staff(conn)
    without = validate_month_staffing("2026-10", [], [requirement], [], staff, role_names)
    withrooms = validate_month_staffing("2026-10", [], [with_rooms], [], staff, role_names)

    assert [d.status for d in without] == [d.status for d in withrooms]
    assert [len(d.issues) for d in without] == [len(d.issues) for d in withrooms]
