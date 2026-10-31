"""人が足りない日に、誰に入ってもらうとよいかの候補（おすすめ）.

勤務表づくりは、出られる人（「できれば休み」の人も含む）を使い切ったうえで不足を最小にしている。
そのため不足が残る日は「いまのルールのままでは入れられる人がもういない日」で、
ここで出すのは「この人に相談すれば埋まる」という候補になる。

候補の種類（決まりを曲げる度合いが小さい順）:
    READY        出勤できる日なのに入っていない人（そのまま入れられる）
    OFF_WEEKDAY  その曜日がふだん休みで、休み希望は出していない人（出勤を相談）
    NO_TIME      ふだんの勤務時間が未登録の人（登録すれば入れられる）
    CONSECUTIVE  出ると連続勤務の上限を超える人（前後に休みが要る）

「必ず休み」の人と無効のスタッフは候補にしない。
足りない役割・スキルを埋められる人だけを出し、同じ種類の中では
自分の役割の人 → 代わりにできる役割の人、その月の勤務時間が少ない人の順に並べる。

画面に依存しない（DBから集めるのは collect_* だけ）。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
import sqlite3

from src.constants import TIME_STATUS_OK
from src.models import StaffDayCondition, StaffDetail
from src.role_assignment import RolePerson, role_shortages

READY = "READY"
OFF_WEEKDAY = "OFF_WEEKDAY"
NO_TIME = "NO_TIME"
CONSECUTIVE = "CONSECUTIVE"
KIND_ORDER = {READY: 0, OFF_WEEKDAY: 1, NO_TIME: 2, CONSECUTIVE: 3}
MAX_SUGGESTIONS = 3


@dataclass(frozen=True)
class DayShortage:
    """1日分の不足（勤務案・保存済みの勤務表のどちらからも作る）."""

    work_date: str
    staff_shortage: int = 0
    role_shortages: dict[int, int] = field(default_factory=dict)
    skill_shortage: int = 0
    required_skill_level: int | None = None

    @property
    def has_shortage(self) -> bool:
        return bool(self.staff_shortage or self.skill_shortage or any(self.role_shortages.values()))


@dataclass(frozen=True)
class Suggestion:
    staff_id: int
    staff_name: str
    work_date: str
    kind: str
    reason: str
    fills: str  # 何を埋めるか（'リーダー' / '代わりにリーダー' / '人数' / 'スキル'）
    month_minutes: int = 0
    needs_extra_day: bool = False  # ふだん休みの曜日 → 「出勤できる」と勤務希望に登録してから入れる

    @property
    def can_apply(self) -> bool:
        """画面のボタンでそのまま入れられるか（勤務時間未登録・連勤超えは不可）."""
        return self.kind in (READY, OFF_WEEKDAY)


# ---------------------------------------------------------------------------
# 不足の計算（保存済みの勤務表用. 勤務案は Solver の結果から作る）
# ---------------------------------------------------------------------------


def shortage_of_day(
    work_date: str,
    working: Iterable[StaffDetail],
    required_total: int | None,
    role_counts: Mapping[int, int],
    required_skill_level: int | None = None,
    required_skill_count: int = 0,
) -> DayShortage:
    """その日の出勤者で、人数・役割・スキルが何人足りないか（代わりにできる役割も数える）."""
    working = list(working)
    people = [RolePerson(d.staff.staff_id, d.staff.role_id, tuple(d.substitute_role_ids)) for d in working]
    roles = role_shortages(people, {rid: n for rid, n in role_counts.items() if n > 0})
    skill = 0
    if required_skill_level is not None and required_skill_count:
        skilled = sum(1 for d in working if d.staff.skill_level >= required_skill_level)
        skill = max(0, required_skill_count - skilled)
    return DayShortage(
        work_date=work_date,
        staff_shortage=max(0, (required_total or 0) - len(working)),
        role_shortages=roles,
        skill_shortage=skill,
        required_skill_level=required_skill_level,
    )


def shortage_from_generation(day, required_skill_level: int | None = None) -> DayShortage:
    """勤務案（Solver の結果）の1日分から."""
    return DayShortage(
        work_date=day.work_date,
        staff_shortage=day.staff_shortage,
        role_shortages=dict(day.role_shortages),
        skill_shortage=day.skill_shortage,
        required_skill_level=required_skill_level,
    )


# ---------------------------------------------------------------------------
# 候補
# ---------------------------------------------------------------------------


def consecutive_if_added(worked: set[str], work_date: str) -> int:
    """その日も出勤したら何日連続になるか."""
    day = date.fromisoformat(work_date)
    run = 1
    for step in (-1, 1):
        cursor = day + timedelta(days=step)
        while cursor.isoformat() in worked:
            run += 1
            cursor += timedelta(days=step)
    return run


def _fit(detail: StaffDetail, shortage: DayShortage, role_names: Mapping[int, str]) -> tuple[int, str] | None:
    """この人で埋まるもの. (並び順の重み, 表示) か、埋められなければ None."""
    short_roles = [rid for rid, n in shortage.role_shortages.items() if n > 0]
    if short_roles:
        if detail.staff.role_id in short_roles:
            return 0, role_names.get(detail.staff.role_id, str(detail.staff.role_id))
        for rid in short_roles:
            if rid in detail.substitute_role_ids:
                return 1, f"代わりに{role_names.get(rid, str(rid))}"
        return None
    if shortage.skill_shortage and shortage.required_skill_level is not None:
        if detail.staff.skill_level >= shortage.required_skill_level:
            return 0, f"スキルLv{shortage.required_skill_level}以上"
        if not shortage.staff_shortage:
            return None
    if shortage.staff_shortage:
        return 0, "人数"
    return None


def _classify(
    detail: StaffDetail, condition: StaffDayCondition | None, worked: set[str], work_date: str
) -> tuple[str, str, bool] | None:
    """(種類, 理由, 勤務希望に「出勤できる」を登録する必要があるか). 候補にしない人は None."""
    if condition is None or condition.absolute_off:
        return None  # 「必ず休み」は尊重する
    if condition.can_work:
        needs_extra = False
        base = "できれば休みの希望あり" if condition.prefer_off else "出勤できる日です"
        time_ok = condition.time_status == TIME_STATUS_OK
    else:
        # ふだん休みの曜日で、休み希望は出していない
        needs_extra = True
        base = "ふだん休みの曜日（休み希望なし）"
        time_ok = bool(detail.staff.standard_start_time and detail.staff.standard_end_time)
    limit = detail.staff.max_consecutive_days
    run = consecutive_if_added(worked, work_date)
    if limit and run > limit:
        return CONSECUTIVE, f"{base}・出ると{run}連勤（上限{limit}）", needs_extra
    if not time_ok:
        return NO_TIME, f"{base}・ふだんの勤務時間が未登録（スタッフ管理で登録すると入れられます）", needs_extra
    return (OFF_WEEKDAY if needs_extra else READY), base, needs_extra


def suggest_for_day(
    shortage: DayShortage,
    staff: Iterable[StaffDetail],
    working_ids: set[int],
    conditions: Mapping[tuple[int, str], StaffDayCondition],
    worked_dates: Mapping[int, set[str]],
    role_names: Mapping[int, str],
    month_minutes: Mapping[int, int] | None = None,
    limit: int = MAX_SUGGESTIONS,
) -> list[Suggestion]:
    """足りない日1日分の候補（多くて limit 人）."""
    if not shortage.has_shortage:
        return []
    month_minutes = month_minutes or {}
    ranked = []
    for order, detail in enumerate(staff):
        sid = detail.staff.staff_id
        if not detail.staff.active or sid in working_ids:
            continue
        fit = _fit(detail, shortage, role_names)
        if fit is None:
            continue
        kind_reason = _classify(detail, conditions.get((sid, shortage.work_date)),
                                worked_dates.get(sid, set()), shortage.work_date)
        if kind_reason is None:
            continue
        kind, reason, needs_extra = kind_reason
        minutes = month_minutes.get(sid, 0)
        suggestion = Suggestion(sid, detail.staff.staff_name, shortage.work_date, kind, reason,
                                fit[1], minutes, needs_extra)
        ranked.append(((KIND_ORDER[kind], fit[0], minutes, order), suggestion))
    ranked.sort(key=lambda item: item[0])
    return [s for _, s in ranked[:limit]]


# ---------------------------------------------------------------------------
# DB から集める
# ---------------------------------------------------------------------------


def collect_suggestions(
    conn: sqlite3.Connection,
    dates: list[str],
    shortages: Iterable[DayShortage],
    working: Mapping[str, set[int]],
) -> dict[str, list[Suggestion]]:
    """work_date -> 候補. working は 期間内の日 -> 出勤する staff_id（勤務案または保存済み）."""
    from src import repositories as repo
    from src import services
    from src.monthly_hours import WorkedShift, monthly_totals

    shortages = [s for s in shortages if s.has_shortage]
    if not shortages:
        return {}
    staff = repo.list_staff_details(conn, include_inactive=False)
    role_names = services.get_role_names(conn)
    conditions = {
        (c.staff_id, c.work_date): c
        for rows in services.get_period_conditions_by_staff(conn, dates).values()
        for c in rows
    }
    # 連勤の判定には、期間の前後に保存済みの勤務も使う
    worked: dict[int, set[str]] = {}
    for history in (services.build_prior_work_history(conn, dates),
                    services.build_following_work_history(conn, dates)):
        for sid, days in history.items():
            worked.setdefault(sid, set()).update(days)
    for work_date, ids in working.items():
        for sid in ids:
            worked.setdefault(sid, set()).add(work_date)
    # 並べ替え用: その月の勤務時間（期間内は勤務案／保存済みの勤務で数える）
    times = {(c.staff_id, c.work_date): (c.effective_start_time, c.effective_end_time) for c in conditions.values()}
    period_shifts = [WorkedShift(sid, d, *times.get((sid, d), (None, None)))
                     for d, ids in working.items() for sid in ids]
    totals = monthly_totals(conn, dates, period_shifts)
    result = {}
    for shortage in shortages:
        month = shortage.work_date[:7]
        minutes = {sid: by_month.get(month).minutes for sid, by_month in totals.items() if by_month.get(month)}
        result[shortage.work_date] = suggest_for_day(
            shortage, staff, working.get(shortage.work_date, set()), conditions, worked, role_names, minutes
        )
    return result


def saved_shortages(conn: sqlite3.Connection, dates: list[str]) -> tuple[list[DayShortage], dict[str, set[int]]]:
    """保存済みの勤務表の不足と、日ごとの出勤者（下書きの日だけ. 確定した日は変えないので除く）."""
    from src import repositories as repo
    from src import services

    schedule = services.get_current_schedule(conn, dates)
    views = {v.work_date: v for v in services.get_period_requirements(conn, dates)}
    details = {d.staff.staff_id: d for d in repo.list_staff_details(conn, include_inactive=True)}
    working: dict[str, set[int]] = {}
    shortages = []
    for day in schedule.days:
        if not day.exists:
            continue
        ids = {sid for sid, a in day.assignments.items() if a.is_working}
        working[day.work_date] = ids
        view = views.get(day.work_date)
        if day.is_finalized or view is None or not view.is_defined:
            continue
        req = view.requirement
        shortages.append(shortage_of_day(
            day.work_date, [details[sid] for sid in ids if sid in details], req.required_total_staff,
            view.role_counts, req.required_skill_level, req.required_skill_count,
        ))
    return [s for s in shortages if s.has_shortage], working


def format_suggestion(s: Suggestion) -> str:
    """'鈴木 和子（クリーナー）：ふだん休みの曜日（休み希望なし）・今月32.5時間'."""
    from src.monthly_hours import format_hours

    return f"{s.staff_name}（{s.fills}）：{s.reason}・今月{format_hours(s.month_minutes)}"


def describe_shortage(shortage: DayShortage, role_names: Mapping[int, str]) -> str:
    """'リーダー1人・人数2人' のように何が足りないか."""
    parts = [f"{role_names.get(rid, rid)}{n}人" for rid, n in sorted(shortage.role_shortages.items()) if n]
    if shortage.staff_shortage:
        parts.append(f"人数{shortage.staff_shortage}人")
    if shortage.skill_shortage:
        parts.append(f"スキル{shortage.skill_shortage}人")
    return "・".join(parts)
