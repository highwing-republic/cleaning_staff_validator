"""1日の役割の割り当て（代わりにできる役割を考える）.

スタッフは自分の役割のほかに「代わりにできる役割」を持てる。1日に受け持てる役割は1つだけ。
その日に出勤する人を、役割ごとの必要人数をできるだけ多く満たすように割り当てる
（最大流. 人数は多くても20人程度なので単純な増加路で十分）。

勤務表づくり（不足の集計）・保存済みの勤務表の検証・体制チェックが同じ考え方を使う。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RolePerson:
    staff_id: int
    role_id: int
    substitute_role_ids: tuple[int, ...] = ()

    def can_fill(self, role_id: int) -> bool:
        return role_id == self.role_id or role_id in self.substitute_role_ids


def assign_roles(
    people: Iterable[RolePerson], requirements: Mapping[int, int]
) -> dict[int, int]:
    """staff_id -> その日に受け持つ役割.

    必要人数のある役割をできるだけ多く満たす。自分の役割で足りるなら代わりには回さない
    （まず自分の役割で埋め、足りない役割だけ代わりで埋める. 足りない分は増加路で入れ替える）。
    どの必要人数にも使わない人は自分の役割のまま。
    """
    people = list(people)
    needed = {role_id: count for role_id, count in requirements.items() if count > 0}
    assigned: dict[int, int | None] = {p.staff_id: None for p in people}
    filled = {role_id: 0 for role_id in needed}
    by_id = {p.staff_id: p for p in people}

    # 1. 自分の役割で埋める
    for person in people:
        if person.role_id in needed and filled[person.role_id] < needed[person.role_id]:
            assigned[person.staff_id] = person.role_id
            filled[person.role_id] += 1

    # 2. 足りない役割を、代わりにできる人で埋める（必要なら入れ替える）
    def augment(role_id: int, visited: set[int]) -> bool:
        for person in people:
            if person.staff_id in visited or not person.can_fill(role_id):
                continue
            visited.add(person.staff_id)
            current = assigned[person.staff_id]
            if current is None:
                assigned[person.staff_id] = role_id
                return True
            # この人を別の役割へ動かせるなら、ここへ回す
            if augment(current, visited):
                assigned[person.staff_id] = role_id
                return True
        return False

    for role_id in needed:
        while filled[role_id] < needed[role_id]:
            before = dict(assigned)
            if not augment(role_id, set()):
                assigned = before
                break
            filled = _count(assigned, needed)

    return {
        sid: (role if role is not None else by_id[sid].role_id)
        for sid, role in assigned.items()
    }


def _count(assigned: Mapping[int, int | None], needed: Mapping[int, int]) -> dict[int, int]:
    filled = {role_id: 0 for role_id in needed}
    for role in assigned.values():
        if role in filled:
            filled[role] += 1
    return filled


def role_shortages(
    people: Iterable[RolePerson], requirements: Mapping[int, int]
) -> dict[int, int]:
    """役割ごとの不足人数（0 の役割は含めない）."""
    people = list(people)
    acting = assign_roles(people, requirements)
    filled: dict[int, int] = {}
    for role in acting.values():
        filled[role] = filled.get(role, 0) + 1
    return {
        role_id: count - filled.get(role_id, 0)
        for role_id, count in requirements.items()
        if count > 0 and filled.get(role_id, 0) < count
    }
