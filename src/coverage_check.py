"""体制チェック（勤務表を作る前に、必ず足りなくなる日と理由を知らせる）.

日ごとに「その日に出られる人」を役割ごとに数え、必要人数と比べる。
出られる人の判定は勤務表づくり（shift_generation）と同じ:
  普段の勤務曜日（または「普段休みの日に勤務できる」）で、「必ず休み」でなく、勤務時間が決まっていること。
出られる人が必要人数より少なければ、どう並べても足りない（＝作る前に分かる不足）。

画面に依存しない。DB からまとめて読み、判定は純粋な関数で行う。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
import sqlite3

from src import repositories as repo
from src import role_assignment
from src import services
from src.constants import TIME_STATUS_OK
from src.models import StaffDayCondition, StaffDetail
from src.period_utils import format_date_short
from src.requirement_defaults import default_role_counts, required_total_for

WEEKDAY_NAMES = "月火水木金土日"


@dataclass(frozen=True)
class RoleCoverage:
    """1日・1役割の、必要人数と出られる人."""

    role_id: int
    role_name: str
    required: int
    # この役割を受け持てる人（自分の役割の人＋代わりにできる人）
    available: tuple[str, ...] = ()
    # 普段なら出られるが「必ず休み」の人（足りない理由として示す）
    off_by_request: tuple[str, ...] = ()
    # 1人1役割で割り当てたときの不足（None なら人数の差で数える）
    missing: int | None = None

    @property
    def shortfall(self) -> int:
        if self.missing is not None:
            return self.missing
        return max(0, self.required - len(self.available))


@dataclass(frozen=True)
class DayCoverage:
    """1日分の体制チェック."""

    work_date: str
    required_total: int
    # 必要人数が未保存で、初期値（平日8名・土日祝7名など）で判定したか
    uses_defaults: bool = False
    available: tuple[str, ...] = ()
    off_by_request: tuple[str, ...] = ()
    # 勤務時間が未設定などで、出られる日でも勤務表に入れられない人（名前, role_id）
    unscheduled: tuple[tuple[str, int], ...] = ()
    roles: tuple[RoleCoverage, ...] = ()
    skill_level: int | None = None
    skill_required: int = 0
    skill_available: int = 0

    @property
    def total_shortfall(self) -> int:
        return max(0, self.required_total - len(self.available))

    @property
    def skill_shortfall(self) -> int:
        if not self.skill_level or not self.skill_required:
            return 0
        return max(0, self.skill_required - self.skill_available)

    @property
    def short_roles(self) -> list[RoleCoverage]:
        return [r for r in self.roles if r.shortfall]

    @property
    def has_problem(self) -> bool:
        return bool(self.total_shortfall or self.short_roles or self.skill_shortfall)

    def role(self, role_id: int) -> RoleCoverage | None:
        return next((r for r in self.roles if r.role_id == role_id), None)


@dataclass(frozen=True)
class CoverageInput:
    """判定に使う材料（DB から読んだもの）. テストでは直接組み立てる."""

    dates: list[str]
    staff: list[StaffDetail]
    conditions: dict[int, list[StaffDayCondition]]
    roles: list[Mapping]
    # work_date -> (必要人数, {role_id: 人数}, スキルLv, スキル人数, 初期値で補ったか)
    requirements: dict[str, tuple[int, dict[int, int], int | None, int, bool]] = field(
        default_factory=dict
    )


def _san(name: str) -> str:
    return f"{name}さん"


def _names(names: Iterable[str]) -> str:
    return "・".join(_san(n) for n in names)


def load_coverage_input(
    conn: sqlite3.Connection, dates: list[str], totals: Mapping[str, int] | None = None
) -> CoverageInput:
    """totals を渡すと、その日は保存前の入力中の人数で判定する.

    役割の内訳は保存するときと同じ決め方にする（自動の日は人数から決め直し、個別に設定した日はそのまま）。
    """
    from src.simple_requirements import roles_for_total

    roles = repo.list_roles(conn)
    requirements = {}
    for view in services.get_period_requirements(conn, dates):
        if totals is not None and view.work_date in totals:
            total = int(totals[view.work_date] or 0)
            req = view.requirement
            requirements[view.work_date] = (
                total,
                {r.role_id: r.required_count for r in roles_for_total(view, total, roles)},
                req.required_skill_level if req else None,
                req.required_skill_count if req else 0,
                False,
            )
        elif view.is_defined:
            req = view.requirement
            requirements[view.work_date] = (
                req.required_total_staff,
                dict(view.role_counts),
                req.required_skill_level,
                req.required_skill_count,
                False,
            )
        else:  # まだ保存していない日は、初期値で判定する
            requirements[view.work_date] = (
                required_total_for(view.work_date),
                default_role_counts(view.work_date, roles),
                None,
                0,
                True,
            )
    return CoverageInput(
        dates=list(dates),
        staff=repo.list_staff_details(conn, include_inactive=False),
        conditions=services.get_period_conditions_by_staff(conn, dates),
        roles=roles,
        requirements=requirements,
    )


def _can_be_scheduled(condition: StaffDayCondition) -> bool:
    return condition.can_work and condition.time_status == TIME_STATUS_OK


def evaluate_coverage(
    data: CoverageInput, excluded: set[tuple[int, str]] = frozenset()
) -> list[DayCoverage]:
    """日ごとの体制チェック. excluded=(staff_id, work_date) はその日休むものとして数える（もしもの判定用）."""
    role_names = {r["role_id"]: r["role_name"] for r in data.roles}
    by_staff_date = {
        (sid, c.work_date): c for sid, conds in data.conditions.items() for c in conds
    }
    result = []
    for work_date in data.dates:
        total, role_counts, skill_level, skill_count, uses_defaults = data.requirements.get(
            work_date, (0, {}, None, 0, True)
        )
        available, off, unscheduled = [], [], []
        by_role_available: dict[int, list[str]] = {}
        by_role_off: dict[int, list[str]] = {}
        people: list[role_assignment.RolePerson] = []
        skill_available = 0
        for detail in data.staff:
            sid = detail.staff.staff_id
            name = detail.staff.staff_name
            role_id = detail.staff.role_id
            fillable = [role_id] + [r for r in detail.substitute_role_ids if r != role_id]
            condition = by_staff_date.get((sid, work_date))
            if condition is None:
                continue
            if (sid, work_date) in excluded:
                if condition.base_available or condition.available_extra:
                    off.append(name)
                    for r in fillable:
                        by_role_off.setdefault(r, []).append(name)
                continue
            if _can_be_scheduled(condition):
                available.append(name)
                people.append(role_assignment.RolePerson(sid, role_id, tuple(detail.substitute_role_ids)))
                for r in fillable:
                    by_role_available.setdefault(r, []).append(name)
                if skill_level and detail.staff.skill_level >= skill_level:
                    skill_available += 1
            elif condition.absolute_off and condition.base_available:
                off.append(name)
                for r in fillable:
                    by_role_off.setdefault(r, []).append(name)
            elif condition.can_work:
                unscheduled.append((name, role_id))
        # 1人1役割（代わりにできる役割を含む）で割り当てたときの不足
        missing = role_assignment.role_shortages(people, role_counts)
        roles = tuple(
            RoleCoverage(
                role_id=role_id,
                role_name=role_names.get(role_id, str(role_id)),
                required=count,
                available=tuple(by_role_available.get(role_id, [])),
                off_by_request=tuple(by_role_off.get(role_id, [])),
                missing=missing.get(role_id, 0),
            )
            for role_id, count in sorted(role_counts.items())
            if count > 0
        )
        result.append(
            DayCoverage(
                work_date=work_date,
                required_total=total,
                uses_defaults=uses_defaults,
                available=tuple(available),
                off_by_request=tuple(off),
                unscheduled=tuple(unscheduled),
                roles=roles,
                skill_level=skill_level,
                skill_required=skill_count or 0,
                skill_available=skill_available,
            )
        )
    return result


def analyze_coverage(
    conn: sqlite3.Connection, dates: list[str], totals: Mapping[str, int] | None = None
) -> list[DayCoverage]:
    """体制チェック. totals（work_date -> 人数）を渡すと、保存前の入力中の人数で判定する."""
    return evaluate_coverage(load_coverage_input(conn, dates, totals))


# ---------------------------------------------------------------------------
# 表示文
# ---------------------------------------------------------------------------


def describe_role_shortfall(role: RoleCoverage) -> str:
    """'リーダー：出られる人がいません（テスト太郎さんが必ず休み）' など."""
    if not role.available:
        text = f"{role.role_name}：出られる人がいません"
    elif len(role.available) >= role.required:
        # 代わりにできる人を数えると足りるが、その人がほかの役割でも必要
        text = (
            f"{role.role_name}：{role.shortfall}人足りません"
            f"（代わりにできる{_names(role.available)}は、ほかの役割でも必要です）"
        )
    else:
        text = f"{role.role_name}：出られる人が{len(role.available)}人です（必要{role.required}人）"
    if role.off_by_request:
        text += f"（{_names(role.off_by_request)}が必ず休み）"
    return text


def describe_problems(day: DayCoverage) -> list[str]:
    """その日の足りない理由（役割ごと → 全体 → スキル）.

    勤務時間が未設定で入れられない人は、その人がいれば足りる不足（同じ役割・全体）にだけ添える。
    """
    lines = []
    for role in day.short_roles:
        text = describe_role_shortfall(role)
        missing = [name for name, role_id in day.unscheduled if role_id == role.role_id]
        if missing:
            text += f"（勤務時間が未設定：{_names(missing)}）"
        lines.append(text)
    if day.total_shortfall:
        text = f"全体：出られる人が{len(day.available)}人です（必要{day.required_total}人）"
        if day.off_by_request and not day.short_roles:
            text += f"（{_names(day.off_by_request)}が必ず休み）"
        if day.unscheduled:
            text += f"（勤務時間が未設定：{_names(name for name, _ in day.unscheduled)}）"
        lines.append(text)
    if day.skill_shortfall:
        lines.append(
            f"スキルLv{day.skill_level}以上：出られる人が{day.skill_available}人です"
            f"（必要{day.skill_required}人）"
        )
    return lines


def problem_days(days: Iterable[DayCoverage]) -> list[DayCoverage]:
    return [d for d in days if d.has_problem]


def absence_warning(
    data: CoverageInput, staff_id: int, work_date: str
) -> str | None:
    """「この日にこの人が必ず休み」にした場合に、足りなくなる役割があれば知らせる文."""
    detail = next((d for d in data.staff if d.staff.staff_id == staff_id), None)
    if detail is None or work_date not in data.dates:
        return None
    before = evaluate_coverage(data)[data.dates.index(work_date)]
    name = detail.staff.staff_name
    if name not in before.available:
        return None  # もともと出られない日（休んでも体制は変わらない）
    after = evaluate_coverage(data, excluded={(staff_id, work_date)})[data.dates.index(work_date)]
    day = format_date_short(work_date)
    # 自分の役割から先に見る（代わりにできる役割の不足も知らせる）
    order = sorted(after.roles, key=lambda r: r.role_id != detail.staff.role_id)
    for role_after in order:
        role_before = before.role(role_after.role_id)
        if role_after.shortfall <= (role_before.shortfall if role_before else 0):
            continue
        if not role_after.available:
            return f"{day}に{_san(name)}が休むと、出られる{role_after.role_name}がいなくなります。"
        return (
            f"{day}に{_san(name)}が休むと、{role_after.role_name}が"
            f"{role_after.shortfall}人足りなくなります。"
        )
    if after.total_shortfall > before.total_shortfall:
        return (
            f"{day}に{_san(name)}が休むと、出られる人が{len(after.available)}人になり、"
            f"必要な{after.required_total}人に足りません。"
        )
    return None


# ---------------------------------------------------------------------------
# スタッフ設定の食い違い
# ---------------------------------------------------------------------------


def max_days_per_week(weekdays: Iterable[int], max_consecutive: int | None) -> float:
    """通常勤務曜日と連続勤務の上限から、1週間に働ける日数の上限（数週間の平均）."""
    days = set(weekdays)
    if not days:
        return 0.0
    if not max_consecutive:
        return float(len(days))
    weeks = 8
    # 動的計画法: 位置ごとに「直前の連続勤務日数」→ 最大勤務日数
    best = {0: 0}
    for i in range(weeks * 7):
        available = (i % 7) in days
        nxt: dict[int, int] = {}
        for run, total in best.items():
            nxt[0] = max(nxt.get(0, -1), total)  # 休む
            if available and run + 1 <= max_consecutive:
                nxt[run + 1] = max(nxt.get(run + 1, -1), total + 1)
        best = nxt
    return max(best.values()) / weeks


def staff_setting_warnings(detail: StaffDetail) -> list[str]:
    """勤務表に入れない・目安どおりに働けない設定の食い違い."""
    staff = detail.staff
    warnings = []
    if not detail.weekdays:
        warnings.append("通常勤務曜日が未設定のため、勤務表に入りません。")
    if not (staff.standard_start_time and staff.standard_end_time):
        warnings.append("通常勤務時間が未設定のため、勤務表に入りません。")
    target = staff.target_days_per_week
    if target and detail.weekdays:
        if target > len(detail.weekdays):
            warnings.append(
                f"週に働く日数の目安が週{target}日ですが、通常勤務曜日は週{len(detail.weekdays)}日です。"
            )
        else:
            limit = max_days_per_week(detail.weekdays, staff.max_consecutive_days)
            if target > limit:
                limit_text = f"{limit:g}"
                warnings.append(
                    f"週に働く日数の目安が週{target}日ですが、通常勤務曜日"
                    f"（{''.join(WEEKDAY_NAMES[w] for w in detail.weekdays)}）と"
                    f"連続{staff.max_consecutive_days}日までの上限では、週{limit_text}日までしか入れません。"
                )
    return warnings


def weekday_label(work_date: str) -> str:
    return WEEKDAY_NAMES[date.fromisoformat(work_date).weekday()]


def explain_shortage(result_day, coverage: DayCoverage | None, role_names: Mapping[int, str]) -> str:
    """勤務案の不足1日分の説明. 何が何人足りないか＋理由（出られる人が足りない／配置の都合）."""
    parts = [f"{role_names.get(rid, rid)}{n}人不足" for rid, n in sorted(result_day.role_shortages.items())]
    if result_day.staff_shortage:
        parts.append(f"人数{result_day.staff_shortage}人不足")
    if result_day.skill_shortage:
        parts.append(f"スキル{result_day.skill_shortage}人不足")
    head = f"{format_date_short(result_day.work_date)}：" + "・".join(parts)
    if coverage is not None and coverage.has_problem:
        return head + "。" + "／".join(describe_problems(coverage))
    return (
        head + "。出られる人はいますが、連続勤務の上限や前後の期間との兼ね合いで配置できませんでした"
        "（どの人の勤務を変えればよいかは、勤務表の手直しで確認してください）。"
    )
