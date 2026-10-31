"""予約・必要人数の「かんたん入力」（1日の必要人数だけ入れる）の保存内容を作る.

かんたん入力で入れるのは 予約室数（任意）・必要人数・備考 だけ。
- 役割の内訳は必要人数から自動で決める（リーダー1・チェッカー1・残りクリーナー）。
  ただし詳しい設定で内訳を個別に変えた日は、その内訳を守る。
- 最大人数・必要スキル・稼働率など、かんたん入力にない項目は保存済みの値をそのまま残す。
- かんたん入力では期間の全日を保存する（「要件未設定」に戻すのは詳しい設定で行う）。

かんたん作成（②の人数入力）も、役割の内訳の決め方を同じにする。
画面に依存しない。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from src.models import DailyRequirementInput, RoleRequirementInput
from src.requirement_defaults import auto_role_requirements, roles_follow_total


@dataclass(frozen=True)
class SimpleEntry:
    """かんたん入力の1日分."""

    required_total_staff: int
    reserved_rooms: int | None = None
    note: str | None = None


def saved_total(view) -> int | None:
    return view.requirement.required_total_staff if view.is_defined else None


def follows_total(view, roles: Iterable[Mapping]) -> bool:
    """その日の役割の内訳が、必要人数から自動で決まる日か（個別に設定した日でないか）."""
    return roles_follow_total(view.role_counts, view.is_defined, saved_total(view), roles)


def roles_for_total(view, total: int, roles: Iterable[Mapping]) -> list[RoleRequirementInput]:
    """必要人数を total にしたときの役割ごとの人数.

    自動の日は total から決め直し、個別に設定した日は保存済みの内訳をそのまま使う。
    """
    roles = list(roles)
    if follows_total(view, roles):
        return auto_role_requirements(view.work_date, total, roles)
    return [
        RoleRequirementInput(view.work_date, role_id, count)
        for role_id, count in view.role_counts.items()
    ]


def build_simple_save(
    views: Iterable,
    entries: Mapping[str, SimpleEntry],
    roles: Iterable[Mapping],
) -> tuple[list[DailyRequirementInput], list[RoleRequirementInput]]:
    """かんたん入力の内容から、期間全日の保存内容（日ごとの条件・役割ごとの人数）を作る."""
    roles = list(roles)
    requirements: list[DailyRequirementInput] = []
    role_requirements: list[RoleRequirementInput] = []
    for view in views:
        entry = entries[view.work_date]
        total = max(0, int(entry.required_total_staff or 0))
        note = (entry.note or "").strip() or None
        if view.is_defined:
            requirement = replace(
                view.requirement,
                required_total_staff=total,
                reserved_rooms=entry.reserved_rooms,
                note=note,
            )
        else:
            requirement = DailyRequirementInput(
                view.work_date, total, note=note, reserved_rooms=entry.reserved_rooms
            )
        requirements.append(requirement)
        role_requirements.extend(roles_for_total(view, total, roles))
    return requirements, role_requirements
