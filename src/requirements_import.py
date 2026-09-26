"""日別必要人数CSV/Excelインポート（§16, T70/T71）.

DB/Streamlitに依存しない純粋ロジック。UI(pages/04_requirements.py)から呼ばれる。
"""

import io
from datetime import date, datetime

import pandas as pd

from src.constants import ROLE_CODES
from src.models import DailyRequirementInput, RoleRequirementInput, ValidationError
from src.month_utils import get_month_dates
from src.validation import validate_daily_requirement

REQUIRED_BASE_COLUMNS = ("日付",)
REQUIRED_TOTAL_STAFF_COLUMNS = ("最低人数", "必要人数")
REQUIRED_COLUMNS = ("日付", "最低人数")
OPTIONAL_COLUMNS = ("稼働率", "最大人数", "備考")


class ImportFormatError(Exception):
    """CSV/Excelの形式・文字コードが読み取れない場合."""


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------


def read_csv_bytes(data: bytes) -> pd.DataFrame:
    """utf-8-sig → cp932 の順で読み込む（§16）. 両方失敗したらImportFormatError."""
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return pd.read_csv(io.BytesIO(data), encoding=encoding, dtype=object)
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception as exc:  # pandasの内部でのdecodeエラー等
            if _looks_like_decode_error(exc):
                continue
            raise
    raise ImportFormatError("文字コードを判別できません。utf-8またはShift-JIS(cp932)で保存してください。")


def _looks_like_decode_error(exc: Exception) -> bool:
    return "decode" in str(exc).lower() or "codec" in str(exc).lower()


def read_excel_bytes(data: bytes) -> pd.DataFrame:
    """Excel(.xlsx)を読み込む（openpyxlエンジン, 先頭シート）."""
    try:
        return pd.read_excel(io.BytesIO(data), engine="openpyxl", sheet_name=0, dtype=object)
    except Exception as exc:
        raise ImportFormatError(f"Excelファイルを読み込めませんでした: {exc}") from exc


# ---------------------------------------------------------------------------
# 正規化
# ---------------------------------------------------------------------------


def _role_column_map(roles: list[dict]) -> dict[str, int]:
    """列名(role_codeまたはrole_name) -> role_id のマップを作る."""
    mapping: dict[str, int] = {}
    for role in roles:
        mapping[role["role_code"]] = role["role_id"]
        mapping[role["role_name"]] = role["role_id"]
    return mapping


def _to_date_str(value: object) -> str | None:
    """日付セルを 'YYYY-MM-DD' に正規化する. 変換できなければNone."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass
        # Excel由来の 'YYYY/MM/DD' 等も許容
        for fmt in ("%Y/%m/%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Excelのシリアル値が数値のまま渡された場合
        try:
            base = date(1899, 12, 30)
            from datetime import timedelta

            return (base + timedelta(days=int(value))).isoformat()
        except (OverflowError, ValueError):
            return None
    return None


def _to_optional_float(value: object) -> tuple[float | None, bool]:
    """空欄はNone。変換できなければ(None, False)。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None, True
    if isinstance(value, str) and not value.strip():
        return None, True
    try:
        return float(value), True
    except (TypeError, ValueError):
        return None, False


def _to_optional_int(value: object) -> tuple[int | None, bool]:
    fval, ok = _to_optional_float(value)
    if not ok:
        return None, False
    if fval is None:
        return None, True
    if fval != int(fval):
        return None, False
    return int(fval), True


def _to_required_int(value: object) -> tuple[int | None, bool]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None, False
    fval, ok = _to_optional_float(value)
    if not ok or fval is None:
        return None, False
    if fval != int(fval):
        return None, False
    return int(fval), True


def normalize_requirements(
    df: pd.DataFrame,
    year_month: str,
    roles: list[dict],
) -> tuple[list[DailyRequirementInput], list[RoleRequirementInput], list[ValidationError]]:
    """CSV/Excel由来のDataFrameを検証しながらモデルへ変換する.

    列: 日付(必須), 稼働率, 最低人数(必須, 旧名「必要人数」も許容), 最大人数, 備考,
    ロール別必要人数(任意, role_codeまたはrole_name)
    - 日付は対象月に含まれること。重複日はエラー。
    - 最低人数列は「最低人数」「必要人数」のどちらでもよい。両方ある場合は「最低人数」を優先する。
    - 行ごとの検証エラーには行番号を含める。
    - エラーを1件でも返した場合、呼び出し側は保存しない。
    """
    errors: list[ValidationError] = []

    columns = list(df.columns)
    missing = [c for c in REQUIRED_BASE_COLUMNS if c not in columns]
    total_staff_col = next((c for c in REQUIRED_TOTAL_STAFF_COLUMNS if c in columns), None)
    if total_staff_col is None:
        missing.append("最低人数")
    if missing:
        errors.append(
            ValidationError(
                code="REQUIREMENT_IMPORT_MISSING_COLUMN",
                message=f"必須列が見つかりません: {', '.join(missing)}",
            )
        )
        return [], [], errors

    role_col_map = _role_column_map(roles)
    role_columns = [c for c in columns if c in role_col_map]

    month_dates = set(get_month_dates(year_month))
    seen_dates: dict[str, int] = {}

    daily_reqs: list[DailyRequirementInput] = []
    role_reqs: list[RoleRequirementInput] = []

    for idx, row in df.iterrows():
        row_no = idx + 2  # ヘッダ行を1行目とみなし、データは2行目から
        raw_date = row.get("日付")
        work_date = _to_date_str(raw_date)
        if work_date is None:
            errors.append(
                ValidationError(
                    code="REQUIREMENT_IMPORT_DATE_INVALID",
                    message=f"{row_no}行目: 日付を読み取れません（YYYY-MM-DD形式で入力してください）。",
                )
            )
            continue

        if work_date not in month_dates:
            errors.append(
                ValidationError(
                    code="REQUIREMENT_IMPORT_DATE_OUT_OF_MONTH",
                    message=f"{row_no}行目: 日付({work_date})が対象月({year_month})に含まれていません。",
                    work_date=work_date,
                )
            )
            continue

        if work_date in seen_dates:
            errors.append(
                ValidationError(
                    code="REQUIREMENT_IMPORT_DUPLICATE_DATE",
                    message=f"{row_no}行目: 日付({work_date})が{seen_dates[work_date]}行目と重複しています。",
                    work_date=work_date,
                )
            )
            continue
        seen_dates[work_date] = row_no

        required_total, required_ok = _to_required_int(row.get(total_staff_col))
        if not required_ok:
            errors.append(
                ValidationError(
                    code="REQUIREMENT_IMPORT_REQUIRED_INVALID",
                    message=f"{row_no}行目: 最低人数は0以上の整数で入力してください。",
                    work_date=work_date,
                )
            )
            continue

        max_total, max_ok = _to_optional_int(row.get("最大人数")) if "最大人数" in columns else (None, True)
        if not max_ok:
            errors.append(
                ValidationError(
                    code="REQUIREMENT_IMPORT_MAX_INVALID",
                    message=f"{row_no}行目: 最大人数は0以上の整数で入力してください。",
                    work_date=work_date,
                )
            )
            continue

        occupancy, occ_ok = _to_optional_float(row.get("稼働率")) if "稼働率" in columns else (None, True)
        if not occ_ok:
            errors.append(
                ValidationError(
                    code="REQUIREMENT_IMPORT_OCCUPANCY_INVALID",
                    message=f"{row_no}行目: 稼働率は数値で入力してください。",
                    work_date=work_date,
                )
            )
            continue

        note = None
        if "備考" in columns:
            raw_note = row.get("備考")
            if raw_note is not None and not (isinstance(raw_note, float) and pd.isna(raw_note)):
                note_text = str(raw_note).strip()
                note = note_text if note_text else None

        row_role_reqs: list[RoleRequirementInput] = []
        role_row_invalid = False
        for col in role_columns:
            role_id = role_col_map[col]
            count, count_ok = _to_optional_int(row.get(col))
            if not count_ok:
                errors.append(
                    ValidationError(
                        code="REQUIREMENT_IMPORT_ROLE_COUNT_INVALID",
                        message=f"{row_no}行目: {col}の必要人数は0以上の整数で入力してください。",
                        work_date=work_date,
                        role_id=role_id,
                    )
                )
                role_row_invalid = True
                continue
            if count is None:
                count = 0
            row_role_reqs.append(RoleRequirementInput(work_date, role_id, count))

        if role_row_invalid:
            continue

        daily_req = DailyRequirementInput(
            work_date=work_date,
            required_total_staff=required_total,
            max_total_staff=max_total,
            occupancy_rate=occupancy,
            note=note,
        )
        row_errors = validate_daily_requirement(daily_req, row_role_reqs)
        if row_errors:
            for e in row_errors:
                errors.append(
                    ValidationError(
                        code=e.code,
                        message=f"{row_no}行目: {e.message}",
                        staff_id=e.staff_id,
                        work_date=e.work_date,
                        role_id=e.role_id,
                        field_name=e.field_name,
                    )
                )
            continue

        daily_reqs.append(daily_req)
        role_reqs.extend(row_role_reqs)

    return daily_reqs, role_reqs, errors
