"""日別必要条件CSV/Excelインポートのテスト."""

import io

import pandas as pd
import pytest
from openpyxl import Workbook

from src.requirements_import import (
    ImportFormatError,
    normalize_requirements,
    read_csv_bytes,
    read_excel_bytes,
)

YM = "2026-10"
ROLES = [
    {"role_id": 1, "role_code": "LEADER", "role_name": "リーダー"},
    {"role_id": 2, "role_code": "CHECKER", "role_name": "チェッカー"},
    {"role_id": 3, "role_code": "CLEANER", "role_name": "クリーナー"},
]


def _csv_bytes(text: str, encoding: str) -> bytes:
    return text.encode(encoding)


BASIC_CSV = "日付,稼働率,必要人数,最大人数,備考\n2026-10-01,0.5,3,5,テスト\n2026-10-02,,2,,\n"


# ---------------------------------------------------------------------------
# read_csv_bytes
# ---------------------------------------------------------------------------


def test_read_csv_bytes_utf8_sig():
    data = _csv_bytes(BASIC_CSV, "utf-8-sig")
    df = read_csv_bytes(data)
    assert list(df["日付"]) == ["2026-10-01", "2026-10-02"]


def test_read_csv_bytes_cp932():
    text = "日付,稼働率,必要人数,最大人数,備考\n2026-10-01,0.5,3,5,繁忙期\n"
    data = text.encode("cp932")
    df = read_csv_bytes(data)
    assert df.iloc[0]["備考"] == "繁忙期"


def test_read_csv_bytes_undecodable_raises_import_format_error():
    # utf-8-sigとしてもcp932としても不正なバイト列
    data = b"\xff\xfe\x00\x81\xff\xff"
    with pytest.raises(ImportFormatError):
        read_csv_bytes(data)


# ---------------------------------------------------------------------------
# read_excel_bytes
# ---------------------------------------------------------------------------


def test_read_excel_bytes_round_trip():
    wb = Workbook()
    ws = wb.active
    ws.append(["日付", "稼働率", "必要人数", "最大人数", "備考"])
    ws.append(["2026-10-01", 0.5, 3, 5, "テスト"])
    buffer = io.BytesIO()
    wb.save(buffer)

    df = read_excel_bytes(buffer.getvalue())
    assert df.iloc[0]["必要人数"] == 3


# ---------------------------------------------------------------------------
# normalize_requirements
# ---------------------------------------------------------------------------


def test_normalize_requirements_basic():
    df = pd.DataFrame(
        [
            {"日付": "2026-10-01", "稼働率": 0.5, "必要人数": 3, "最大人数": 5, "備考": "テスト"},
            {"日付": "2026-10-02", "稼働率": None, "必要人数": 2, "最大人数": None, "備考": None},
        ]
    )
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert errors == []
    assert len(daily) == 2
    assert daily[0].work_date == "2026-10-01"
    assert daily[0].required_total_staff == 3
    assert daily[1].occupancy_rate is None
    assert daily[1].note is None


def test_normalize_requirements_role_columns_by_code():
    df = pd.DataFrame(
        [{"日付": "2026-10-01", "必要人数": 3, "LEADER": 1, "CHECKER": 1, "CLEANER": 1}]
    )
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert errors == []
    assert {r.role_id for r in role} == {1, 2, 3}
    assert all(r.required_count == 1 for r in role)


def test_normalize_requirements_role_columns_by_name():
    df = pd.DataFrame(
        [{"日付": "2026-10-01", "必要人数": 2, "リーダー": 1, "クリーナー": 1}]
    )
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert errors == []
    assert len(role) == 2


def test_normalize_requirements_missing_required_column():
    df = pd.DataFrame([{"日付": "2026-10-01"}])
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert daily == []
    assert role == []
    assert len(errors) == 1
    assert "最低人数" in errors[0].message


def test_normalize_requirements_accepts_minimum_staff_column_name():
    df = pd.DataFrame(
        [{"日付": "2026-10-01", "稼働率": 0.5, "最低人数": 3, "最大人数": 5, "備考": "テスト"}]
    )
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert errors == []
    assert len(daily) == 1
    assert daily[0].required_total_staff == 3


def test_normalize_requirements_prefers_minimum_staff_when_both_columns_present():
    df = pd.DataFrame(
        [{"日付": "2026-10-01", "最低人数": 3, "必要人数": 9}]
    )
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert errors == []
    assert daily[0].required_total_staff == 3


def test_normalize_requirements_out_of_month_date():
    df = pd.DataFrame([{"日付": "2026-11-01", "必要人数": 1}])
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert daily == []
    assert len(errors) == 1
    assert "対象月" in errors[0].message
    assert "2行目" in errors[0].message


def test_normalize_requirements_duplicate_date():
    df = pd.DataFrame(
        [
            {"日付": "2026-10-01", "必要人数": 1},
            {"日付": "2026-10-01", "必要人数": 2},
        ]
    )
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert len(daily) == 1
    assert len(errors) == 1
    assert "重複" in errors[0].message
    assert "3行目" in errors[0].message


def test_normalize_requirements_invalid_number():
    df = pd.DataFrame([{"日付": "2026-10-01", "必要人数": "abc"}])
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert daily == []
    assert len(errors) == 1
    assert "2行目" in errors[0].message


def test_normalize_requirements_row_error_includes_row_number():
    df = pd.DataFrame(
        [
            {"日付": "2026-10-01", "必要人数": 5, "最大人数": 3},
        ]
    )
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert daily == []
    assert len(errors) == 1
    assert "2行目" in errors[0].message
    assert "最大人数" in errors[0].message


def test_normalize_requirements_empty_optional_cells_become_none():
    df = pd.DataFrame(
        [{"日付": "2026-10-01", "必要人数": 1, "稼働率": "", "最大人数": "", "備考": ""}]
    )
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert errors == []
    assert daily[0].occupancy_rate is None
    assert daily[0].max_total_staff is None
    assert daily[0].note is None


def test_normalize_requirements_role_sum_above_minimum_is_accepted():
    """最低人数は完全一致人数ではないため、ロール合計が上回っても取り込める."""
    df = pd.DataFrame([{"日付": "2026-10-01", "最低人数": 5, "LEADER": 3, "CHECKER": 3}])
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert errors == []
    assert daily[0].required_total_staff == 5
    assert sum(r.required_count for r in role) == 6


def test_normalize_requirements_skill_columns():
    df = pd.DataFrame(
        [
            {"日付": "2026-10-01", "最低人数": 5, "必要スキルLv": 4, "必要スキル人数": 2},
            {"日付": "2026-10-02", "最低人数": 5, "必要スキルLv": "", "必要スキル人数": ""},
        ]
    )
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert errors == []
    assert (daily[0].required_skill_level, daily[0].required_skill_count) == (4, 2)
    assert (daily[1].required_skill_level, daily[1].required_skill_count) == (None, 0)


def test_normalize_requirements_skill_columns_absent_default_to_no_condition():
    df = pd.DataFrame([{"日付": "2026-10-01", "最低人数": 5}])
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert errors == []
    assert (daily[0].required_skill_level, daily[0].required_skill_count) == (None, 0)


@pytest.mark.parametrize(
    "level,count,fragment",
    [
        ("abc", 1, "必要スキルLv"),
        (4, "x", "必要スキル人数"),
        (6, 1, "必要スキルレベル"),
        ("", 2, "必要スキルレベル"),
    ],
)
def test_normalize_requirements_skill_errors(level, count, fragment):
    df = pd.DataFrame(
        [{"日付": "2026-10-01", "最低人数": 5, "必要スキルLv": level, "必要スキル人数": count}]
    )
    daily, role, errors = normalize_requirements(df, YM, ROLES)
    assert daily == []
    assert len(errors) == 1
    assert "2行目" in errors[0].message
    assert fragment in errors[0].message
