"""期間別勤務希望のDB制約・repository・serviceのテスト."""

import sqlite3

import pytest

from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database
from src.models import StaffDatePreferenceInput
from src.period_utils import period_dates
from src.validation import (
    PREFERENCE_ABSOLUTE_OFF_CONFLICT,
    PREFERENCE_DATE_OUT_OF_PERIOD,
    PREFERENCE_DUPLICATED_DATE,
    PREFERENCE_OVERRIDE_TIME_INVALID,
    PREFERENCE_STAFF_MISMATCH,
    PREFERENCE_STAFF_NOT_FOUND,
)

CLEANER = 3
DATES = period_dates("2026-10-20", 14)
FIRST, LAST = DATES[0], DATES[-1]


@pytest.fixture()
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


@pytest.fixture()
def staff_id(conn):
    return repo.create_staff(
        conn, "0001", "Aさん", CLEANER, 4, "清掃",
        standard_start_time="09:00", standard_end_time="15:30",
        weekdays=[0, 1, 3, 4, 5],
    )


def pref(staff_id, work_date, **kwargs) -> StaffDatePreferenceInput:
    return StaffDatePreferenceInput(staff_id=staff_id, work_date=work_date, **kwargs)


def _raw_insert(conn, staff_id, work_date, **columns):
    names = ["staff_id", "work_date", "created_at", "updated_at", *columns]
    placeholders = ", ".join("?" for _ in names)
    values = [staff_id, work_date, "x", "x", *columns.values()]
    with conn:
        conn.execute(
            f"INSERT INTO staff_date_preferences ({', '.join(names)}) VALUES ({placeholders})",
            values,
        )


# ---------------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------------


def test_table_columns(conn):
    columns = [row[1] for row in conn.execute("PRAGMA table_info(staff_date_preferences)")]
    assert columns == [
        "preference_id",
        "staff_id",
        "work_date",
        "absolute_off",
        "prefer_off",
        "available_extra",
        "override_start_time",
        "override_end_time",
        "note",
        "created_at",
        "updated_at",
    ]


def test_no_period_table_is_created(conn):
    """期間そのものはDBエンティティにしない（日付で持つ）."""
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "work_periods" not in tables
    assert "preference_batches" not in tables


def test_unique_staff_and_date(conn, staff_id):
    _raw_insert(conn, staff_id, FIRST, prefer_off=1)
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, staff_id, FIRST, absolute_off=1)


def test_staff_foreign_key(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, 999, FIRST, prefer_off=1)


@pytest.mark.parametrize("work_date", ["2026-10-20", "2026-11-02", "2028-02-29"])
def test_valid_work_dates_accepted(conn, staff_id, work_date):
    _raw_insert(conn, staff_id, work_date, prefer_off=1)


@pytest.mark.parametrize(
    "work_date",
    ["2026-13-01", "2026-02-30", "2026-02-29", "2026-00-10", "2026-10-32", "2026-2-3", "20261020"],
)
def test_invalid_work_dates_rejected(conn, staff_id, work_date):
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, staff_id, work_date, prefer_off=1)


@pytest.mark.parametrize("column", ["absolute_off", "prefer_off", "available_extra"])
@pytest.mark.parametrize("value", [2, -1])
def test_boolean_columns_reject_other_values(conn, staff_id, column, value):
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, staff_id, FIRST, **{column: value})


@pytest.mark.parametrize("value", ["9:00", "24:00", "30:00", "09:60", "0900"])
def test_override_time_requires_hhmm(conn, staff_id, value):
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, staff_id, FIRST, override_start_time=value)


def test_override_end_must_be_after_start(conn, staff_id):
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, staff_id, FIRST, override_start_time="13:00", override_end_time="10:00")


def test_absolute_off_conflicts_rejected_by_db(conn, staff_id):
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, staff_id, FIRST, absolute_off=1, available_extra=1)
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(conn, staff_id, FIRST, absolute_off=1, override_end_time="13:00")


def test_absolute_off_with_note_allowed(conn, staff_id):
    _raw_insert(conn, staff_id, FIRST, absolute_off=1, note="通院")
    saved = repo.get_staff_date_preference(conn, staff_id, FIRST)
    assert saved.absolute_off is True
    assert saved.note == "通院"


# ---------------------------------------------------------------------------
# repository: 保存 → 再取得
# ---------------------------------------------------------------------------


def test_missing_preference_returns_none(conn, staff_id):
    """行がない = 通常条件を使う."""
    assert repo.get_staff_date_preference(conn, staff_id, FIRST) is None


def test_round_trip_all_fields(conn, staff_id):
    repo.upsert_staff_date_preference(
        conn,
        pref(staff_id, "2026-10-22", prefer_off=True,
             override_start_time="10:00", override_end_time="13:00", note="通院"),
    )
    saved = repo.get_staff_date_preference(conn, staff_id, "2026-10-22")
    assert saved.staff_id == staff_id
    assert saved.work_date == "2026-10-22"
    assert saved.absolute_off is False
    assert saved.prefer_off is True
    assert saved.available_extra is False
    assert (saved.override_start_time, saved.override_end_time) == ("10:00", "13:00")
    assert saved.note == "通院"


def test_override_time_normalized_on_save(conn, staff_id):
    repo.upsert_staff_date_preference(
        conn, pref(staff_id, FIRST, override_start_time="9:30", override_end_time="13:00")
    )
    saved = repo.get_staff_date_preference(conn, staff_id, FIRST)
    assert saved.override_start_time == "09:30"


def test_blank_note_saved_as_none(conn, staff_id):
    repo.upsert_staff_date_preference(
        conn, pref(staff_id, FIRST, prefer_off=True, note="   ")
    )
    assert repo.get_staff_date_preference(conn, staff_id, FIRST).note is None


def test_update_replaces_values(conn, staff_id):
    repo.upsert_staff_date_preference(conn, pref(staff_id, FIRST, absolute_off=True))
    repo.upsert_staff_date_preference(
        conn, pref(staff_id, FIRST, prefer_off=True, override_end_time="13:00")
    )
    saved = repo.get_staff_date_preference(conn, staff_id, FIRST)
    assert saved.absolute_off is False
    assert saved.prefer_off is True
    assert saved.override_end_time == "13:00"
    count = conn.execute("SELECT COUNT(*) FROM staff_date_preferences").fetchone()[0]
    assert count == 1


def test_empty_preference_deletes_row(conn, staff_id):
    """すべて通常に戻したら行を削除する（行がない = 通常 を維持）."""
    repo.upsert_staff_date_preference(conn, pref(staff_id, FIRST, absolute_off=True))
    repo.upsert_staff_date_preference(conn, pref(staff_id, FIRST))
    assert repo.get_staff_date_preference(conn, staff_id, FIRST) is None


def test_delete_staff_date_preference(conn, staff_id):
    repo.upsert_staff_date_preference(conn, pref(staff_id, FIRST, absolute_off=True))
    repo.delete_staff_date_preference(conn, staff_id, FIRST)
    assert repo.get_staff_date_preference(conn, staff_id, FIRST) is None


def test_delete_is_idempotent(conn, staff_id):
    repo.delete_staff_date_preference(conn, staff_id, FIRST)


def test_list_staff_preferences_is_ordered_and_bounded(conn, staff_id):
    for work_date in ("2026-10-19", "2026-10-25", "2026-10-22", "2026-11-03"):
        repo.upsert_staff_date_preference(conn, pref(staff_id, work_date, prefer_off=True))
    listed = repo.list_staff_preferences(conn, staff_id, FIRST, LAST)
    assert [p.work_date for p in listed] == ["2026-10-22", "2026-10-25"]


def test_list_staff_preferences_only_returns_target_staff(conn, staff_id):
    other = repo.create_staff(conn, "0002", "Bさん", CLEANER)
    repo.upsert_staff_date_preference(conn, pref(staff_id, FIRST, prefer_off=True))
    repo.upsert_staff_date_preference(conn, pref(other, FIRST, absolute_off=True))
    listed = repo.list_staff_preferences(conn, staff_id, FIRST, LAST)
    assert [p.staff_id for p in listed] == [staff_id]


def test_list_preferences_in_period_covers_every_staff(conn, staff_id):
    other = repo.create_staff(conn, "0002", "Bさん", CLEANER)
    repo.upsert_staff_date_preference(conn, pref(staff_id, FIRST, prefer_off=True))
    repo.upsert_staff_date_preference(conn, pref(other, LAST, absolute_off=True))
    listed = repo.list_preferences_in_period(conn, FIRST, LAST)
    assert {(p.staff_id, p.work_date) for p in listed} == {(staff_id, FIRST), (other, LAST)}


# ---------------------------------------------------------------------------
# repository: 期間まとめ保存
# ---------------------------------------------------------------------------


def test_save_period_saves_only_changed_days(conn, staff_id):
    repo.save_staff_period_preferences(
        conn, staff_id, DATES,
        [
            pref(staff_id, "2026-10-22", absolute_off=True, note="通院"),
            pref(staff_id, "2026-10-24", override_end_time="13:00"),
            pref(staff_id, "2026-10-25", available_extra=True),
            pref(staff_id, "2026-10-26"),           # 空 → 保存されない
            pref(staff_id, "2026-11-01", note="学校行事"),
        ],
    )
    listed = repo.list_staff_preferences(conn, staff_id, FIRST, LAST)
    assert [p.work_date for p in listed] == [
        "2026-10-22", "2026-10-24", "2026-10-25", "2026-11-01"
    ]


def test_save_period_removes_days_not_included(conn, staff_id):
    """期間内の希望を全置換する（通常へ戻した日は行が消える）."""
    repo.save_staff_period_preferences(
        conn, staff_id, DATES,
        [
            pref(staff_id, "2026-10-22", absolute_off=True),
            pref(staff_id, "2026-10-24", prefer_off=True),
        ],
    )
    repo.save_staff_period_preferences(
        conn, staff_id, DATES, [pref(staff_id, "2026-10-24", prefer_off=True)]
    )
    assert [p.work_date for p in repo.list_staff_preferences(conn, staff_id, FIRST, LAST)] == [
        "2026-10-24"
    ]


def test_save_period_does_not_touch_other_periods(conn, staff_id):
    repo.upsert_staff_date_preference(conn, pref(staff_id, "2026-10-10", absolute_off=True))
    repo.upsert_staff_date_preference(conn, pref(staff_id, "2026-11-20", absolute_off=True))
    repo.save_staff_period_preferences(conn, staff_id, DATES, [])
    remaining = [
        p.work_date for p in repo.list_staff_preferences(conn, staff_id, "2026-01-01", "2026-12-31")
    ]
    assert remaining == ["2026-10-10", "2026-11-20"]


def test_save_period_does_not_touch_other_staff(conn, staff_id):
    other = repo.create_staff(conn, "0002", "Bさん", CLEANER)
    repo.upsert_staff_date_preference(conn, pref(other, "2026-10-22", absolute_off=True))
    repo.save_staff_period_preferences(conn, staff_id, DATES, [])
    assert repo.get_staff_date_preference(conn, other, "2026-10-22") is not None


def test_save_period_rejects_date_out_of_period(conn, staff_id):
    with pytest.raises(ValueError):
        repo.save_staff_period_preferences(
            conn, staff_id, DATES, [pref(staff_id, "2026-12-01", prefer_off=True)]
        )


def test_save_period_rejects_mismatched_staff(conn, staff_id):
    other = repo.create_staff(conn, "0002", "Bさん", CLEANER)
    with pytest.raises(ValueError):
        repo.save_staff_period_preferences(
            conn, staff_id, DATES, [pref(other, FIRST, prefer_off=True)]
        )


def test_save_period_rolls_back_on_failure(conn, staff_id):
    """14日分の途中で失敗したら一部の日だけ保存された状態にならない."""
    repo.save_staff_period_preferences(
        conn, staff_id, DATES, [pref(staff_id, "2026-10-21", prefer_off=True)]
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_staff_period_preferences(
            conn, staff_id, DATES,
            [
                pref(staff_id, "2026-10-23", prefer_off=True),
                # DB制約違反（絶対休み＋通常外勤務可）
                pref(staff_id, "2026-10-24", absolute_off=True, available_extra=True),
            ],
        )
    listed = [p.work_date for p in repo.list_staff_preferences(conn, staff_id, FIRST, LAST)]
    assert listed == ["2026-10-21"]


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------


def test_service_saves_valid_preferences(conn, staff_id):
    errors = services.save_period_preferences(
        conn, staff_id, DATES,
        [
            pref(staff_id, "2026-10-22", absolute_off=True, note="通院"),
            pref(staff_id, "2026-10-24", prefer_off=True, override_end_time="13:00"),
        ],
    )
    assert errors == []
    assert len(repo.list_staff_preferences(conn, staff_id, FIRST, LAST)) == 2


def test_service_rejects_contradiction_without_saving(conn, staff_id):
    errors = services.save_period_preferences(
        conn, staff_id, DATES,
        [
            pref(staff_id, "2026-10-22", prefer_off=True),
            pref(staff_id, "2026-10-23", absolute_off=True, override_end_time="13:00"),
        ],
    )
    assert PREFERENCE_ABSOLUTE_OFF_CONFLICT in [e.code for e in errors]
    assert repo.list_staff_preferences(conn, staff_id, FIRST, LAST) == []


def test_service_rejects_invalid_time_without_saving(conn, staff_id):
    errors = services.save_period_preferences(
        conn, staff_id, DATES, [pref(staff_id, "2026-10-22", override_end_time="9時")]
    )
    assert PREFERENCE_OVERRIDE_TIME_INVALID in [e.code for e in errors]
    assert repo.list_staff_preferences(conn, staff_id, FIRST, LAST) == []


def test_service_rejects_unknown_staff(conn):
    errors = services.save_period_preferences(conn, 999, DATES, [])
    assert [e.code for e in errors] == [PREFERENCE_STAFF_NOT_FOUND]


def test_service_rejects_out_of_period_date(conn, staff_id):
    errors = services.save_period_preferences(
        conn, staff_id, DATES, [pref(staff_id, "2026-12-01", prefer_off=True)]
    )
    assert PREFERENCE_DATE_OUT_OF_PERIOD in [e.code for e in errors]


def test_service_rejects_duplicated_date(conn, staff_id):
    errors = services.save_period_preferences(
        conn, staff_id, DATES,
        [
            pref(staff_id, "2026-10-22", prefer_off=True),
            pref(staff_id, "2026-10-22", absolute_off=True),
        ],
    )
    assert PREFERENCE_DUPLICATED_DATE in [e.code for e in errors]


def test_service_allows_standard_time_unset(conn):
    """通常勤務時間が未設定でも転記を止めない（画面側で警告する）."""
    staff_id = repo.create_staff(conn, "0009", "Cさん", CLEANER, weekdays=[0, 1, 2, 3, 4])
    errors = services.save_period_preferences(
        conn, staff_id, DATES, [pref(staff_id, "2026-10-22", override_end_time="13:00")]
    )
    assert errors == []


def test_service_period_conditions_merge_saved_preferences(conn, staff_id):
    services.save_period_preferences(
        conn, staff_id, DATES,
        [
            pref(staff_id, "2026-10-22", absolute_off=True),
            pref(staff_id, "2026-10-24", override_end_time="13:00"),
        ],
    )
    detail = repo.get_staff_detail(conn, staff_id)
    by_date = {c.work_date: c for c in services.get_period_conditions(conn, detail, DATES)}
    assert len(by_date) == len(DATES)
    assert by_date["2026-10-22"].can_work is False
    assert by_date["2026-10-24"].effective_end_time == "13:00"
    assert by_date["2026-10-20"].effective_end_time == "15:30"


def test_service_conditions_by_staff_excludes_inactive(conn, staff_id):
    inactive = repo.create_staff(conn, "0002", "Bさん", CLEANER, weekdays=[0])
    repo.deactivate_staff(conn, inactive)
    by_staff = services.get_period_conditions_by_staff(conn, DATES)
    assert set(by_staff) == {staff_id}
    assert len(by_staff[staff_id]) == len(DATES)


def test_service_conditions_by_staff_with_empty_period(conn, staff_id):
    assert services.get_period_conditions_by_staff(conn, []) == {}


def test_service_save_rejects_mismatched_staff_as_a_validation_error(conn, staff_id):
    """staff_id が食い違う希望は未処理の ValueError ではなく検証エラーで返す."""
    other = repo.create_staff(conn, "0002", "Bさん", CLEANER)
    errors = services.save_period_preferences(
        conn, staff_id, DATES, [pref(other, FIRST, prefer_off=True)]
    )
    assert [e.code for e in errors] == [PREFERENCE_STAFF_MISMATCH]
