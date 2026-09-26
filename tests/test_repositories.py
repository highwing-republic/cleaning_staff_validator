import sqlite3

import pytest

from src.database import get_connection, initialize_database
from src.models import (
    AttendanceShiftInput,
    DailyRequirementInput,
    RoleRequirementInput,
)
from src import repositories as repo


@pytest.fixture()
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# staff
# ---------------------------------------------------------------------------


def test_list_roles(conn):
    roles = repo.list_roles(conn)
    assert [r["role_code"] for r in roles] == ["LEADER", "CHECKER", "CLEANER"]


def test_create_get_and_list_staff(conn):
    staff_id = repo.create_staff(conn, "0015", "山田", 1, 4, "清掃")
    s = repo.get_staff(conn, staff_id)
    assert s.employee_code == "0015"
    assert s.staff_name == "山田"
    assert s.role_id == 1
    assert s.skill_level == 4
    assert s.department == "清掃"
    assert s.active is True

    assert [x.staff_id for x in repo.list_staff(conn)] == [staff_id]
    assert repo.get_staff(conn, 999) is None


def test_create_staff_defaults(conn):
    s = repo.get_staff(conn, repo.create_staff(conn, "1", "山田", 3))
    assert s.skill_level == 3
    assert s.department is None


def test_employee_code_leading_zero_preserved_and_distinct(conn):
    id1 = repo.create_staff(conn, "0015", "A", 3)
    id2 = repo.create_staff(conn, "15", "B", 3)
    assert repo.get_staff_by_employee_code(conn, "0015").staff_id == id1
    assert repo.get_staff_by_employee_code(conn, "15").staff_id == id2
    assert repo.get_staff_by_employee_code(conn, "015") is None


def test_create_staff_duplicate_employee_code_rejected(conn):
    repo.create_staff(conn, "0001", "A", 3)
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_staff(conn, "0001", "B", 3)
    assert len(repo.list_staff(conn)) == 1


def test_list_staff_include_inactive(conn):
    id1 = repo.create_staff(conn, "1", "A", 1)
    id2 = repo.create_staff(conn, "2", "B", 1)
    repo.deactivate_staff(conn, id2)

    all_staff = repo.list_staff(conn, include_inactive=True)
    assert {s.staff_id for s in all_staff} == {id1, id2}

    active_only = repo.list_staff(conn, include_inactive=False)
    assert {s.staff_id for s in active_only} == {id1}


def test_update_staff(conn):
    staff_id = repo.create_staff(conn, "0001", "山田", 1, 2)
    repo.update_staff(
        conn,
        staff_id,
        employee_code="0002",
        staff_name="鈴木",
        role_id=2,
        skill_level=5,
        department="清掃",
    )
    s = repo.get_staff(conn, staff_id)
    assert (s.employee_code, s.staff_name, s.role_id, s.skill_level, s.department) == (
        "0002", "鈴木", 2, 5, "清掃"
    )


def test_update_staff_not_found(conn):
    with pytest.raises(ValueError):
        repo.update_staff(
            conn, 999, employee_code="1", staff_name="x", role_id=1, skill_level=3
        )


def test_update_staff_duplicate_employee_code_rejected(conn):
    repo.create_staff(conn, "0001", "A", 1)
    id2 = repo.create_staff(conn, "0002", "B", 1)
    with pytest.raises(sqlite3.IntegrityError):
        repo.update_staff(
            conn, id2, employee_code="0001", staff_name="B", role_id=1, skill_level=3
        )
    assert repo.get_staff(conn, id2).employee_code == "0002"


def test_deactivate_staff(conn):
    staff_id = repo.create_staff(conn, "1", "山田", 1)
    repo.deactivate_staff(conn, staff_id)
    assert repo.get_staff(conn, staff_id).active is False


def test_deactivate_staff_not_found(conn):
    with pytest.raises(ValueError):
        repo.deactivate_staff(conn, 999)


# ---------------------------------------------------------------------------
# requirements
# ---------------------------------------------------------------------------


def test_save_get_daily_requirement(conn):
    req = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5)
    repo.save_daily_requirement(conn, req)
    reqs = repo.get_daily_requirements(conn, "2026-10")
    assert len(reqs) == 1
    assert reqs[0].required_total_staff == 5


def test_save_daily_requirement_upsert(conn):
    repo.save_daily_requirement(conn, DailyRequirementInput(work_date="2026-10-01", required_total_staff=5))
    repo.save_daily_requirement(conn, DailyRequirementInput(work_date="2026-10-01", required_total_staff=8))
    reqs = repo.get_daily_requirements(conn, "2026-10")
    assert len(reqs) == 1
    assert reqs[0].required_total_staff == 8


def test_get_daily_requirements_month_filter(conn):
    repo.save_daily_requirement(conn, DailyRequirementInput(work_date="2026-09-30", required_total_staff=1))
    repo.save_daily_requirement(conn, DailyRequirementInput(work_date="2026-10-01", required_total_staff=2))
    repo.save_daily_requirement(conn, DailyRequirementInput(work_date="2026-11-01", required_total_staff=3))
    reqs = repo.get_daily_requirements(conn, "2026-10")
    assert [r.work_date for r in reqs] == ["2026-10-01"]


def test_save_daily_requirements_bulk_all_or_nothing(conn):
    good = DailyRequirementInput(work_date="2026-10-01", required_total_staff=5)
    # required_total_staff NOT NULL -> passing None violates the constraint
    bad = DailyRequirementInput(work_date="2026-10-02", required_total_staff=None)
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_daily_requirements(conn, [good, bad])
    assert repo.get_daily_requirements(conn, "2026-10") == []


def test_save_get_role_requirement(conn):
    req = RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=2)
    repo.save_role_requirement(conn, req)
    reqs = repo.get_role_requirements(conn, "2026-10")
    assert len(reqs) == 1
    assert reqs[0].required_count == 2


def test_save_role_requirement_upsert(conn):
    repo.save_role_requirement(conn, RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=2))
    repo.save_role_requirement(conn, RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=4))
    reqs = repo.get_role_requirements(conn, "2026-10")
    assert len(reqs) == 1
    assert reqs[0].required_count == 4


def test_save_role_requirements_bulk_all_or_nothing(conn):
    good = RoleRequirementInput(work_date="2026-10-01", role_id=1, required_count=2)
    bad = RoleRequirementInput(work_date="2026-10-02", role_id=999, required_count=1)  # FK violation
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_role_requirements(conn, [good, bad])
    assert repo.get_role_requirements(conn, "2026-10") == []


def test_get_role_requirements_month_filter(conn):
    repo.save_role_requirement(conn, RoleRequirementInput(work_date="2026-09-30", role_id=1, required_count=1))
    repo.save_role_requirement(conn, RoleRequirementInput(work_date="2026-10-15", role_id=1, required_count=1))
    reqs = repo.get_role_requirements(conn, "2026-10")
    assert [r.work_date for r in reqs] == ["2026-10-15"]


def test_daily_requirement_skill_round_trip(conn):
    repo.save_daily_requirement(
        conn,
        DailyRequirementInput(
            work_date="2026-10-01", required_total_staff=5,
            required_skill_level=4, required_skill_count=2,
        ),
    )
    (req,) = repo.get_daily_requirements(conn, "2026-10")
    assert (req.required_skill_level, req.required_skill_count) == (4, 2)

    # upsertでスキル条件を解除できる
    repo.save_daily_requirement(
        conn,
        DailyRequirementInput(
            work_date="2026-10-01", required_total_staff=5,
            required_skill_level=None, required_skill_count=0,
        ),
    )
    (req,) = repo.get_daily_requirements(conn, "2026-10")
    assert (req.required_skill_level, req.required_skill_count) == (None, 0)


def test_daily_requirement_skill_count_without_level_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_daily_requirement(
            conn,
            DailyRequirementInput(
                work_date="2026-10-01", required_total_staff=5, required_skill_count=1
            ),
        )


# ---------------------------------------------------------------------------
# attendance
# ---------------------------------------------------------------------------


def _shift(code, work_date, *, staff_id=None, shift_type="TIME_RANGE", raw="09:00-15:30"):
    is_range = shift_type == "TIME_RANGE"
    return AttendanceShiftInput(
        employee_code=code,
        work_date=work_date,
        raw_shift=raw,
        shift_type=shift_type,
        available_for_cleaning=is_range,
        staff_id=staff_id,
        employee_name=f"名前{code}",
        department="清掃",
        start_minutes=540 if is_range else None,
        end_minutes=930 if is_range else None,
    )


def test_save_attendance_import_counts_and_round_trip(conn):
    staff_id = repo.create_staff(conn, "0001", "山田", 3)
    shifts = [
        _shift("0001", "2026-09-01", staff_id=staff_id),
        _shift("0001", "2026-09-02", staff_id=staff_id, shift_type="BLANK", raw=""),
        _shift("0099", "2026-09-01"),  # 未登録
        _shift("0099", "2026-09-02", shift_type="UNKNOWN", raw="??"),
        _shift("0098", "2026-09-01", shift_type="OTHER_DUTY", raw="深夜フロント（夜勤）"),
    ]
    import_id = repo.save_attendance_import(conn, "2026-09", "shift.csv", shifts)

    active = repo.get_active_import(conn, "2026-09")
    assert active.import_id == import_id
    assert active.status == "ACTIVE"
    assert active.source_filename == "shift.csv"
    assert active.employee_count == 3
    assert active.unmatched_count == 2
    assert active.shift_count == 4  # BLANK以外

    loaded = repo.list_attendance_shifts(conn, import_id)
    key = lambda s: (s.work_date, s.employee_code)  # noqa: E731
    assert sorted(loaded, key=key) == sorted(shifts, key=key)


def test_reimport_supersedes_without_deleting(conn):
    first = repo.save_attendance_import(conn, "2026-09", "v1.csv", [_shift("1", "2026-09-01")])
    other_month = repo.save_attendance_import(
        conn, "2026-10", "oct.csv", [_shift("1", "2026-10-01")]
    )
    second = repo.save_attendance_import(conn, "2026-09", "v2.csv", [_shift("1", "2026-09-02")])

    assert repo.get_active_import(conn, "2026-09").import_id == second
    history = repo.list_attendance_imports(conn, "2026-09")
    assert [(h.import_id, h.status) for h in history] == [
        (second, "ACTIVE"),
        (first, "SUPERSEDED"),
    ]
    # 旧取込の明細は物理削除されない
    assert len(repo.list_attendance_shifts(conn, first)) == 1
    # 別月のACTIVEは影響を受けない
    assert repo.get_active_import(conn, "2026-10").import_id == other_month


def test_failed_import_keeps_previous_active(conn):
    first = repo.save_attendance_import(conn, "2026-09", "v1.csv", [_shift("1", "2026-09-01")])
    duplicate = [_shift("1", "2026-09-01"), _shift("1", "2026-09-01")]
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_attendance_import(conn, "2026-09", "bad.csv", duplicate)

    assert repo.get_active_import(conn, "2026-09").import_id == first
    assert [h.import_id for h in repo.list_attendance_imports(conn, "2026-09")] == [first]


def test_import_rejects_out_of_month_date(conn):
    with pytest.raises(ValueError):
        repo.save_attendance_import(conn, "2026-09", "a.csv", [_shift("1", "2026-10-01")])
    assert repo.get_active_import(conn, "2026-09") is None


def test_get_active_import_none(conn):
    assert repo.get_active_import(conn, "2026-09") is None
    assert repo.list_attendance_imports(conn, "2026-09") == []
