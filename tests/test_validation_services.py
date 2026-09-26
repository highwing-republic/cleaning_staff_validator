"""services.validate_month: ACTIVE取込 × 日別必要条件 の月間検証."""

import pytest

from attendance_csv import make_csv
from src import repositories as repo
from src import services
from src import staffing_validation as sv
from src.database import get_connection, initialize_database
from src.models import DailyRequirementInput, RoleRequirementInput
from src.month_utils import get_month_dates

YM = "2026-09"
DATES = get_month_dates(YM)
LEADER, CHECKER, CLEANER = 1, 2, 3


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


def _import(conn, data=None, name="shift.csv"):
    preview = services.preview_attendance_csv(conn, make_csv() if data is None else data, name)
    return services.import_attendance(conn, preview)


def _day(result, work_date):
    return next(d for d in result.days if d.work_date == work_date)


def test_no_active_import_returns_empty_result(conn):
    repo.save_daily_requirements(conn, [DailyRequirementInput(d, 1) for d in DATES])
    result = services.validate_month(conn, YM)
    assert result.has_import is False
    assert result.days == []
    assert services.NO_ACTIVE_IMPORT_MESSAGE == "この月の勤怠シフトが取り込まれていません。"


def test_validate_month_from_active_import(conn):
    # 既定CSV: 0015 清掃TIME_RANGE / 0016 清掃・深夜フロント / 0020 朝TIME_RANGE（毎日）
    repo.create_staff(conn, "0015", "テスト清掃A", LEADER, 4, "清掃")
    _import(conn)
    repo.save_daily_requirements(conn, [DailyRequirementInput(d, 1) for d in DATES[:10]])
    repo.save_role_requirements(conn, [RoleRequirementInput(DATES[0], LEADER, 1)])
    repo.save_daily_requirement(conn, DailyRequirementInput(DATES[1], 2))

    result = services.validate_month(conn, YM)
    assert result.has_import
    assert result.import_record.source_filename == "shift.csv"
    assert len(result.days) == 30

    first = _day(result, DATES[0])
    assert first.actual_cleaning_staff == 1  # 夜勤・朝は数えない
    assert first.actual_roles[LEADER] == 1
    assert first.status == "OK"

    second = _day(result, DATES[1])
    assert second.status == "ERROR"
    assert [i.code for i in second.issues] == [sv.STAFF_SHORTAGE]

    assert result.requirement_missing_days == 20
    assert result.count_status("OK") == 9
    assert result.count_status("ERROR") == 1
    assert result.count_status("WARNING") == 20


def test_unmatched_cleaning_staff_counted_in_total(conn):
    _import(conn)  # 全員未登録
    repo.save_daily_requirements(conn, [DailyRequirementInput(d, 1) for d in DATES])
    day = _day(services.validate_month(conn, YM), DATES[0])
    assert day.actual_cleaning_staff == 1
    assert day.unmatched_working_count == 1
    assert day.status == "WARNING"
    assert [i.code for i in day.issues] == [sv.UNMATCHED_STAFF]


def test_unknown_shift_from_csv_is_uncertain(conn):
    repo.create_staff(conn, "0015", "テスト清掃A", CLEANER, 3, "清掃")
    repo.create_staff(conn, "0016", "テスト清掃B", CLEANER, 3, "清掃")
    _import(conn, make_csv(cells={("0015", DATES[0]): "特別勤務A"}))
    repo.save_daily_requirements(conn, [DailyRequirementInput(d, 1) for d in DATES])
    day = _day(services.validate_month(conn, YM), DATES[0])
    assert day.actual_cleaning_staff == 0
    assert day.unknown_cleaning_shift_count == 1
    assert day.status == "WARNING"
    assert [i.code for i in day.issues] == [sv.STAFF_UNCERTAIN, sv.UNKNOWN_SHIFT]


def test_uses_only_active_import(conn):
    repo.create_staff(conn, "0015", "テスト清掃A", CLEANER, 3, "清掃")
    repo.save_daily_requirements(conn, [DailyRequirementInput(d, 1) for d in DATES])
    _import(conn, make_csv(), "old.csv")
    # 新しい取込では 9/1 の清掃勤務が空欄
    _import(conn, make_csv(cells={("0015", DATES[0]): ""}), "new.csv")

    result = services.validate_month(conn, YM)
    assert result.import_record.source_filename == "new.csv"
    assert _day(result, DATES[0]).actual_cleaning_staff == 0
    assert _day(result, DATES[0]).status == "ERROR"
    assert _day(result, DATES[1]).status == "OK"


def test_inactive_staff_uses_master_role(conn):
    staff_id = repo.create_staff(conn, "0015", "テスト清掃A", LEADER, 3, "清掃")
    repo.deactivate_staff(conn, staff_id)
    _import(conn)
    repo.save_daily_requirement(conn, DailyRequirementInput(DATES[0], 1))
    repo.save_role_requirements(conn, [RoleRequirementInput(DATES[0], LEADER, 1)])
    day = _day(services.validate_month(conn, YM), DATES[0])
    assert day.actual_roles[LEADER] == 1
    assert day.status == "OK"
