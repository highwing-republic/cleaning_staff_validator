"""勤怠CSV取込: staff master 照合・プレビュー・保存（トランザクション）のテスト."""

import dataclasses
import sqlite3

import pytest

from attendance_csv import make_csv
from src import attendance_import as ai
from src import repositories as repo
from src import services
from src.database import get_connection, initialize_database

LEADER, CHECKER, CLEANER = 1, 2, 3


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    initialize_database(c)
    yield c
    c.close()


def _preview(conn, data=None, name="shift.csv"):
    return services.preview_attendance_csv(conn, make_csv() if data is None else data, name)


def _cell(preview, code, work_date="2026-09-01"):
    return next(
        s for s in preview.shifts if (s.employee_code, s.work_date) == (code, work_date)
    )


def _warning_codes(preview, employee_code=None):
    return [
        w.code for w in preview.warnings
        if employee_code is None or w.employee_code == employee_code
    ]


def _table_counts(conn):
    return (
        conn.execute("SELECT COUNT(*) FROM attendance_imports").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM attendance_shifts").fetchone()[0],
    )


# ---------------------------------------------------------------------------
# staff master 照合
# ---------------------------------------------------------------------------


def test_exact_match_by_employee_code(conn):
    staff_id = repo.create_staff(conn, "0015", "テスト清掃A", CLEANER, 4, "清掃")
    preview = _preview(conn)
    assert preview.errors == []
    assert all(s.staff_id == staff_id for s in preview.shifts if s.employee_code == "0015")
    assert _warning_codes(preview, "0015") == []


def test_match_does_not_strip_leading_zero(conn):
    repo.create_staff(conn, "15", "テスト清掃A", CLEANER, 3, "清掃")
    preview = _preview(conn)  # CSVは "0015"
    assert _cell(preview, "0015").staff_id is None
    assert services.UNMATCHED_STAFF in _warning_codes(preview, "0015")


def test_no_fallback_to_name(conn):
    repo.create_staff(conn, "9999", "テスト清掃A", CLEANER, 3, "清掃")  # 同名・別番号
    assert _cell(_preview(conn), "0015").staff_id is None


def test_unmatched_staff_kept_as_cleaning_candidate(conn):
    preview = _preview(conn)
    cell = _cell(preview, "0015")
    assert cell.staff_id is None
    assert cell.available_for_cleaning is True  # 清掃 + TIME_RANGE は未登録でも人数候補
    assert preview.unmatched_count == 3  # 種類数（セル数ではない）
    unmatched = [w for w in preview.warnings if w.code == services.UNMATCHED_STAFF]
    assert [w.employee_code for w in unmatched] == ["0015", "0016", "0020"]
    assert preview.can_import


def test_name_mismatch_is_warning_but_matched(conn):
    staff_id = repo.create_staff(conn, "0015", "旧姓テスト", CLEANER, 3, "清掃")
    preview = _preview(conn)
    assert _cell(preview, "0015").staff_id == staff_id
    assert _warning_codes(preview, "0015") == [services.NAME_MISMATCH]
    assert preview.name_mismatch_count == 1


def test_department_mismatch_uses_csv_department(conn):
    # master は清掃だがCSVは朝 → CSVを正として清掃勤務ではない
    id_morning = repo.create_staff(conn, "0020", "テスト朝A", CLEANER, 3, "清掃")
    # master は朝だがCSVは清掃 → 清掃勤務
    id_cleaning = repo.create_staff(conn, "0015", "テスト清掃A", CLEANER, 3, "朝")
    preview = _preview(conn)

    assert _cell(preview, "0020").staff_id == id_morning
    assert _cell(preview, "0020").available_for_cleaning is False
    assert _cell(preview, "0015").staff_id == id_cleaning
    assert _cell(preview, "0015").available_for_cleaning is True
    assert _warning_codes(preview, "0020") == [services.DEPARTMENT_MISMATCH]
    assert _warning_codes(preview, "0015") == [services.DEPARTMENT_MISMATCH]
    assert preview.department_mismatch_count == 2


def test_master_without_department_is_not_mismatch(conn):
    repo.create_staff(conn, "0015", "テスト清掃A", CLEANER, 3, None)
    assert _warning_codes(_preview(conn), "0015") == []


def test_blank_csv_name_is_not_name_mismatch(conn):
    repo.create_staff(conn, "0015", "テスト清掃A", CLEANER, 3, "清掃")
    data = make_csv(employees=[("0015", "", "清掃", "09:00-15:30")])
    assert _warning_codes(_preview(conn, data), "0015") == [ai.EMPLOYEE_NAME_BLANK]


def test_inactive_staff_is_warning_and_not_excluded(conn):
    staff_id = repo.create_staff(conn, "0015", "テスト清掃A", CLEANER, 3, "清掃")
    repo.deactivate_staff(conn, staff_id)
    preview = _preview(conn)
    cell = _cell(preview, "0015")
    assert cell.staff_id == staff_id
    assert cell.available_for_cleaning is True
    assert _warning_codes(preview, "0015") == [services.INACTIVE_STAFF]
    assert preview.inactive_staff_count == 1


def test_preview_counts(conn):
    cells = {
        ("0015", "2026-09-02"): "",
        ("0015", "2026-09-03"): "特別勤務A",
        ("0020", "2026-09-04"): "30:00-23:00",
    }
    preview = _preview(conn, make_csv(cells=cells))
    assert preview.year_month == "2026-09"
    assert preview.employee_count == 3
    assert preview.shift_count == 3 * 30 - 1  # BLANK以外（OTHER_DUTY・UNKNOWNを含む）
    assert preview.unknown_shift_count == 2
    assert preview.cleaning_employee_count == 2  # 0015, 0016（部門=清掃）
    assert preview.unmatched_count == 3


def test_preview_does_not_write_db(conn):
    _preview(conn)
    assert _table_counts(conn) == (0, 0)


# ---------------------------------------------------------------------------
# 保存・トランザクション
# ---------------------------------------------------------------------------


def test_first_import_saves_all_cells(conn):
    repo.create_staff(conn, "0015", "テスト清掃A", CLEANER, 3, "清掃")
    cells = {("0015", "2026-09-02"): "", ("0020", "2026-09-03"): "特別勤務A"}
    preview = _preview(conn, make_csv(cells=cells), "C:\\Users\\someone\\shift_2026-09.csv")
    import_id = services.import_attendance(conn, preview)

    active = repo.get_active_import(conn, "2026-09")
    assert active.import_id == import_id
    assert active.source_filename == "shift_2026-09.csv"  # basenameのみ
    assert (active.employee_count, active.shift_count, active.unmatched_count) == (
        preview.employee_count, preview.shift_count, preview.unmatched_count
    )
    assert (active.employee_count, active.unmatched_count) == (3, 2)

    stored = repo.list_attendance_shifts(conn, import_id)
    assert len(stored) == 3 * 30  # BLANKを含む全従業員 × 全日付
    by_key = {(s.employee_code, s.work_date): s for s in stored}
    assert by_key[("0015", "2026-09-02")].shift_type == "BLANK"
    assert by_key[("0015", "2026-09-02")].raw_shift == ""
    assert by_key[("0020", "2026-09-03")].raw_shift == "特別勤務A"
    assert by_key[("0015", "2026-09-01")].staff_id is not None
    assert by_key[("0020", "2026-09-01")].staff_id is None
    assert by_key[("0015", "2026-09-01")].available_for_cleaning is True
    assert (by_key[("0015", "2026-09-01")].start_minutes, by_key[("0015", "2026-09-01")].end_minutes) == (540, 930)


def test_reimport_supersedes_previous_active(conn):
    first = services.import_attendance(conn, _preview(conn, name="v1.csv"))
    second = services.import_attendance(conn, _preview(conn, name="v2.csv"))

    history = repo.list_attendance_imports(conn, "2026-09")
    assert [(h.import_id, h.status, h.source_filename) for h in history] == [
        (second, "ACTIVE", "v2.csv"),
        (first, "SUPERSEDED", "v1.csv"),
    ]
    assert len(repo.list_attendance_shifts(conn, first)) == 90  # 旧明細は残る


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe\x00\x81\xff\xff",
        make_csv(extra_columns=["備考"]),
        make_csv(employees=[("0015", "A", "清掃", ""), ("0015", "B", "清掃", "")]),
        make_csv(dates=["2026-09-01"]),
    ],
    ids=["decode", "unknown_column", "duplicate_code", "partial_month"],
)
def test_fatal_error_writes_nothing_and_keeps_active(conn, data):
    first = services.import_attendance(conn, _preview(conn))
    before = _table_counts(conn)

    preview = _preview(conn, data)
    assert preview.errors
    assert not preview.can_import
    with pytest.raises(ValueError):
        services.import_attendance(conn, preview)

    assert _table_counts(conn) == before
    assert repo.get_active_import(conn, "2026-09").import_id == first


def test_failed_save_rolls_back_and_keeps_active(conn):
    first = services.import_attendance(conn, _preview(conn, name="v1.csv"))
    before = _table_counts(conn)

    preview = _preview(conn, name="v2.csv")
    # 保存途中（明細INSERT）で失敗させる: 存在しない staff_id は外部キー違反
    broken_last = dataclasses.replace(preview.shifts[-1], staff_id=999)
    broken = dataclasses.replace(preview, shifts=preview.shifts[:-1] + [broken_last])
    with pytest.raises(sqlite3.IntegrityError):
        services.import_attendance(conn, broken)

    assert _table_counts(conn) == before
    active = repo.get_active_import(conn, "2026-09")
    assert (active.import_id, active.status, active.source_filename) == (first, "ACTIVE", "v1.csv")


def test_other_duty_saved_as_not_cleaning(conn):
    import_id = services.import_attendance(conn, _preview(conn))
    other = [s for s in repo.list_attendance_shifts(conn, import_id) if s.shift_type == "OTHER_DUTY"]
    assert len(other) == 30
    assert all(not s.available_for_cleaning for s in other)
    assert all(s.raw_shift == "深夜フロント（夜勤）" for s in other)


def test_list_active_imports_across_months(conn):
    sep = services.import_attendance(conn, _preview(conn, make_csv("2026-09")))
    oct_ = services.import_attendance(conn, _preview(conn, make_csv("2026-10")))
    services.import_attendance(conn, _preview(conn, make_csv("2026-09"), "v2.csv"))
    active = repo.list_active_imports(conn)
    assert [a.year_month for a in active] == ["2026-10", "2026-09"]
    assert active[0].import_id == oct_
    assert active[1].import_id != sep
