"""かんたん作成の進行管理。計算ルールは既存のservice/Solverに委譲する。"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

from src import repositories as repo, services
from src.models import DailyRequirementInput, ScheduleGenerationResult, StaffDatePreferenceInput
from src.period_utils import parse_date, period_dates
from src.requirement_defaults import (
    WEEKDAY_REQUIRED_TOTAL,
    WEEKEND_REQUIRED_TOTAL,
)
from src.shift_generation import generate_shift
from src.simple_requirements import roles_for_total


class WorkflowError(ValueError):
    """利用者が次の操作で解決できるエラー。"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def initialize(conn):
    """既存の業務テーブルには手を加えない追加スキーマ。"""
    with conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS guided_periods (
            start_date TEXT NOT NULL, days INTEGER NOT NULL CHECK(days BETWEEN 1 AND 14),
            step INTEGER NOT NULL DEFAULT 2 CHECK(step BETWEEN 1 AND 4),
            preferences_review TEXT, requirements_review TEXT,
            updated_at TEXT NOT NULL, PRIMARY KEY(start_date, days))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS guided_defaults (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            weekday_staff INTEGER NOT NULL CHECK(weekday_staff>=0),
            weekend_staff INTEGER NOT NULL CHECK(weekend_staff>=0))""")


@contextmanager
def _write(conn):
    if conn.in_transaction:
        raise RuntimeError("guided workflow requires an idle connection")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _normalize(value):
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, dict):
        return [[_normalize(k), _normalize(v)] for k, v in sorted(value.items(), key=lambda x: repr(x[0]))]
    if isinstance(value, (set, frozenset)):
        return [_normalize(v) for v in sorted(value)]
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(_normalize(value), ensure_ascii=False).encode()).hexdigest()


def input_fingerprint(conn, dates):
    """Solver入力に加え、未作成・未固定行・確定状態・無効スタッフも検出。"""
    request = services.build_regeneration_request(conn, dates)
    return _digest((request, services.get_current_schedule(conn, dates),
                    repo.list_staff_details(conn, include_inactive=True)))


def review_fingerprint(conn, dates, kind):
    if kind == "preferences":
        return _digest((repo.list_staff_details(conn, include_inactive=False),
                        repo.list_preferences_in_period(conn, dates[0], dates[-1])))
    if kind == "requirements":
        return _digest(services.get_period_requirements(conn, dates))
    raise ValueError("unknown review kind")


def start_period(conn, start_date, days):
    dates = period_dates(start_date, days)
    with conn:
        conn.execute("""INSERT INTO guided_periods(start_date,days,updated_at) VALUES(?,?,?)
            ON CONFLICT(start_date,days) DO UPDATE SET updated_at=excluded.updated_at""",
                     (dates[0], days, _now()))
    return dates


def list_periods(conn):
    """既存画面で作成済みの期間も再開・印刷の一覧に含める。"""
    periods = {(r["start_date"], r["days"]): dict(r) for r in
               conn.execute("SELECT * FROM guided_periods ORDER BY updated_at DESC")}
    for row in conn.execute("SELECT period_start,period_end,MAX(created_at) AS updated_at FROM schedule_runs GROUP BY period_start,period_end"):
        days = (parse_date(row["period_end"]) - parse_date(row["period_start"])).days + 1
        if 1 <= days <= 14:
            periods.setdefault((row["period_start"], days),
                               dict(start_date=row["period_start"], days=days, step=3, updated_at=row["updated_at"]))
    for period in periods.values():
        dates = period_dates(period["start_date"], period["days"])
        current = services.get_current_schedule(conn, dates)
        period["complete"] = len(current.finalized_dates) == len(dates)
        period["saved_days"] = len(current.existing_dates)
    return sorted(periods.values(), key=lambda p: (p["updated_at"], p["start_date"]), reverse=True)


def get_progress(conn, start_date, days):
    dates = period_dates(start_date, days)
    row = conn.execute("SELECT * FROM guided_periods WHERE start_date=? AND days=?", (start_date, days)).fetchone()
    current = services.get_current_schedule(conn, dates)
    if len(current.finalized_dates) == days:
        return 4
    if row is None:
        return 3 if current.existing_dates else 2
    if (row["preferences_review"] != review_fingerprint(conn, dates, "preferences") or
            row["requirements_review"] != review_fingerprint(conn, dates, "requirements")):
        return 2
    return min(row["step"], 3 if current.missing_dates else 4)


def set_step(conn, start_date, days, step):
    with conn:
        conn.execute("UPDATE guided_periods SET step=?,updated_at=? WHERE start_date=? AND days=?",
                     (step, _now(), start_date, days))


def confirm_inputs(conn, start_date, days):
    dates = period_dates(start_date, days)
    if any(not d.is_defined for d in services.get_period_requirements(conn, dates)):
        raise WorkflowError("必要人数が未入力の日があります。人数を保存してから進んでください。")
    if not repo.list_staff(conn, include_inactive=False):
        raise WorkflowError("スタッフが未登録です。「スタッフ設定」から登録してください。")
    with conn:
        conn.execute("""UPDATE guided_periods SET preferences_review=?,requirements_review=?,step=3,updated_at=?
            WHERE start_date=? AND days=?""", (review_fingerprint(conn, dates, "preferences"),
            review_fingerprint(conn, dates, "requirements"), _now(), start_date, days))


def get_defaults(conn):
    """平日・土日祝の基本人数. 未保存なら、ふだんの体制（役割の最低人数の合計）."""
    row = conn.execute("SELECT * FROM guided_defaults WHERE singleton=1").fetchone()
    if row:
        return row["weekday_staff"], row["weekend_staff"]
    return WEEKDAY_REQUIRED_TOTAL, WEEKEND_REQUIRED_TOTAL


def save_defaults(conn, weekday_staff, weekend_staff):
    if any(type(n) is not int or n < 0 for n in (weekday_staff, weekend_staff)):
        raise WorkflowError("必要人数には0以上の整数を入力してください。")
    with conn:
        conn.execute("INSERT INTO guided_defaults VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE SET weekday_staff=excluded.weekday_staff,weekend_staff=excluded.weekend_staff",
                     (weekday_staff, weekend_staff))


def save_staff_counts(conn, dates, counts, expected_fingerprint):
    """簡易入力で変更しないスキル・最大人数などをそのまま保持する。

    役割の内訳は人数に合わせて自動で決める（リーダー1・チェッカー1・残りクリーナー）。
    ただし詳しい設定で内訳を個別に変えた日は、その内訳を守る。
    """
    with _write(conn):
        if review_fingerprint(conn, dates, "requirements") != expected_fingerprint:
            raise WorkflowError("必要人数が別の画面で変更されました。画面を読み直して確認してください。")
        current = services.get_period_requirements(conn, dates)
        requirements = [replace(view.requirement, required_total_staff=counts[view.work_date])
                        if view.is_defined else DailyRequirementInput(view.work_date, counts[view.work_date])
                        for view in current]
        role_master = repo.list_roles(conn)
        roles = [role for view in current
                 for role in roles_for_total(view, counts[view.work_date], role_master)]
        errors = services.save_period_requirements(conn, dates, requirements, roles)
        if errors:
            raise WorkflowError("\n".join(e.message for e in errors))


@dataclass(frozen=True)
class Preview:
    work_dates: list[str]
    fingerprint: str
    result: ScheduleGenerationResult


def make_preview(conn, dates):
    if any(not d.is_defined for d in services.get_period_requirements(conn, dates)):
        raise WorkflowError("必要人数が未入力です。「休み・人数の確認」に戻って保存してください。")
    fingerprint = input_fingerprint(conn, dates)
    request = services.build_regeneration_request(conn, dates)
    active_ids = {staff.staff_id for staff in request.staff}
    current = services.get_current_schedule(conn, dates)
    if any(a.is_working and a.staff_id not in active_ids for d in current.days for a in d.assignments.values()):
        raise WorkflowError("勤務表に無効スタッフの出勤が残っています。スタッフ設定と詳細な勤務表調整で確認してください。")
    result = generate_shift(request)
    if fingerprint != input_fingerprint(conn, dates):
        raise WorkflowError("作成中に入力内容が変わりました。もう一度勤務表を作成してください。")
    if not result.has_solution:
        raise WorkflowError("勤務表を作成できませんでした。変更しない勤務と、休み・最大人数・連勤上限が矛盾していないか確認してください。")
    return Preview(list(dates), fingerprint, result)


def save_preview(conn, preview):
    dates = preview.work_dates
    if not preview.result.has_solution or preview.result.work_dates != dates:
        raise WorkflowError("保存できる勤務案がありません。もう一度作成してください。")
    with _write(conn):
        if input_fingerprint(conn, dates) != preview.fingerprint:
            raise WorkflowError("作成後に入力や勤務表が変わりました。古い案は保存せず、もう一度作成してください。")
        current = services.get_current_schedule(conn, dates)
        now = _now()
        run_id = repo._insert_run(conn, dates[0], dates[-1], "REGENERATE" if current.existing_dates else "INITIAL",
                                  preview.result.solver_status, preview.result.total_shortage,
                                  preview.result.total_workdays, now)
        for view in current.days:
            if not view.is_finalized:
                conn.execute(repo._UPSERT_DAY_SQL, (view.work_date, "DRAFT", run_id, now))
        for assignment in preview.result.assignments:
            view = current.day(assignment.work_date)
            existing = view.assignments.get(assignment.staff_id)
            if view.is_finalized or (existing and existing.locked):
                continue
            conn.execute(repo._UPSERT_ASSIGNMENT_SQL, (assignment.work_date, assignment.staff_id,
                int(assignment.is_working), assignment.start_time, assignment.end_time,
                0, "GENERATED", run_id, now))
    return run_id


def save_manual_change(conn, dates, staff_id, work_date, is_working, locked, expected_fingerprint):
    """保存する直前の状態で検証。最大人数・前後の連勤も確認する。"""
    if work_date not in dates:
        raise WorkflowError("対象期間外の日付です。")
    with _write(conn):
        if input_fingerprint(conn, dates) != expected_fingerprint:
            raise WorkflowError("勤務表が変更されました。画面を読み直してから修正してください。")
        _apply_manual_change(conn, staff_id, work_date, is_working, locked)


def assign_suggestion(conn, dates, staff_id, work_date, needs_extra_day, expected_fingerprint):
    """不足日の候補の人を出勤にする（自動作成で変えないよう固定する）.

    その曜日がふだん休みの人は、本人に相談して出られることになった前提で、
    その日の勤務希望に「通常休みだが勤務可能」を付けてから出勤にする（1トランザクション）。
    """
    if work_date not in dates:
        raise WorkflowError("対象期間外の日付です。")
    with _write(conn):
        if input_fingerprint(conn, dates) != expected_fingerprint:
            raise WorkflowError("勤務表が変更されました。画面を読み直してから修正してください。")
        if needs_extra_day:
            current = repo.get_staff_date_preference(conn, staff_id, work_date)
            if current is not None and current.absolute_off:
                raise WorkflowError("この日は「必ず休み」の希望が出ています。")
            # ③で自分で決めた変更なので、②の「休みの希望を確認しました」が有効なら有効のまま残す
            # （残さないと、開き直したときに②へ戻されて確認し直しになる）
            row = conn.execute("SELECT preferences_review FROM guided_periods WHERE start_date=? AND days=?",
                               (dates[0], len(dates))).fetchone()
            was_confirmed = row is not None and row["preferences_review"] == review_fingerprint(conn, dates, "preferences")
            pref = replace(current, available_extra=True) if current else StaffDatePreferenceInput(
                staff_id=staff_id, work_date=work_date, available_extra=True)
            repo._upsert_preference(conn, pref, repo._now())  # 出勤の保存と同じトランザクションで
            if was_confirmed:
                conn.execute("UPDATE guided_periods SET preferences_review=?,updated_at=? WHERE start_date=? AND days=?",
                             (review_fingerprint(conn, dates, "preferences"), _now(), dates[0], len(dates)))
        _apply_manual_change(conn, staff_id, work_date, True, True)


def _apply_manual_change(conn, staff_id, work_date, is_working, locked):
    detail = repo.get_staff_detail(conn, staff_id)
    errors = services.validate_manual_schedule_changes(conn, staff_id, {work_date: (is_working, locked)})
    if errors:
        raise WorkflowError("\n".join(e.message for e in errors))
    if is_working:
        if not detail.staff.active:
            raise WorkflowError("無効スタッフを新たに出勤にすることはできません。")
        view = services.get_current_schedule(conn, [work_date]).day(work_date)
        requirement = services.get_period_requirements(conn, [work_date])[0].requirement
        count = sum(a.is_working for sid, a in view.assignments.items() if sid != staff_id) + 1
        if requirement and requirement.max_total_staff is not None and count > requirement.max_total_staff:
            raise WorkflowError("最大人数を超えます。別のスタッフの勤務または最大人数の設定を確認してください。")
        limit = detail.staff.max_consecutive_days
        if limit:
            day = parse_date(work_date)
            first, last = day - timedelta(days=limit), day + timedelta(days=limit)
            worked = {a.work_date for a in repo.list_staff_schedule_assignments(conn, staff_id, first.isoformat(), last.isoformat()) if a.is_working}
            worked.add(work_date)
            consecutive = 1
            for direction in (-1, 1):
                cursor = day + timedelta(days=direction)
                while cursor.isoformat() in worked:
                    consecutive += 1
                    cursor += timedelta(days=direction)
            if consecutive > limit:
                raise WorkflowError(f"この変更では{consecutive}日連続の勤務になります。上限の{limit}日以内になるよう休みを入れてください。")
    errors = services.save_manual_schedule_changes(conn, staff_id, {work_date: (is_working, locked)})
    if errors:
        raise WorkflowError("\n".join(e.message for e in errors))


def finalize_period(conn, dates, expected_fingerprint, acknowledge_issues=False):
    with _write(conn):
        if input_fingerprint(conn, dates) != expected_fingerprint:
            raise WorkflowError("確認後に勤務表や入力が変わりました。内容を確認し直してください。")
        validation = services.validate_current_schedule(conn, dates)
        if validation.missing_days or validation.requirement_missing_days:
            raise WorkflowError("未作成の日または必要人数が未入力の日があります。作成・入力を終えてください。")
        if validation.problem_days and not acknowledge_issues:
            raise WorkflowError("不足などの注意事項を確認してから確定してください。")
        now = _now()
        conn.executemany("UPDATE schedule_days SET status='FINALIZED',finalized_at=?,updated_at=? WHERE work_date=? AND status='DRAFT'",
                         [(now, now, d) for d in dates])
