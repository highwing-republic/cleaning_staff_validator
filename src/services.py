"""画面から呼ばれる業務処理.

UIはこのモジュール経由でDB・検証を組み合わせる。
勤怠取込・日別検証のフローは Phase 2 で追加する。
"""

import sqlite3

from src import repositories as repo


def get_role_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["role_id"]: r["role_name"] for r in repo.list_roles(conn)}
