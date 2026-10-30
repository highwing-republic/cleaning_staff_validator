"""日数のプルダウン入力（「未設定」も選べる）.

数値の入力欄は、空欄のときに −／＋ ボタンが押せず、キーボードで数字を打つしかない。
初めて使う人には「入力できない」ように見えるため、任意の日数はプルダウンで選ぶ。
保存済みの値が選択肢にない場合（例 20日）は、その値を選択肢に加えて
編集画面を開いて保存しただけで値が変わらないようにする。

選択肢の組み立てと表示文はStreamlitに依存しない関数にしてある。
"""

from collections.abc import Iterable

import streamlit as st

UNSET_LABEL = "未設定"


def count_options(choices: Iterable[int], current: int | None) -> list[int | None]:
    """先頭が「未設定」(None) の選択肢. 保存済みの値が範囲外なら加える."""
    options = list(choices)
    if current is not None and current not in options:
        options = sorted([*options, current])
    return [None, *options]


def format_count(value: int | None, unit: str, unset_label: str = UNSET_LABEL) -> str:
    """unit は '週{}日' のような書式. None は unset_label（既定は「未設定」）."""
    return unset_label if value is None else unit.format(value)


def count_select(
    label: str,
    choices: Iterable[int],
    value: int | None,
    key: str,
    *,
    unit: str,
    unset_label: str = UNSET_LABEL,
    help: str | None = None,
) -> int | None:
    """日数をプルダウンで選ぶ. 未設定なら None を返す."""
    options = count_options(choices, value)
    return st.selectbox(
        label,
        options,
        index=options.index(value),
        format_func=lambda n: format_count(n, unit, unset_label),
        key=key,
        help=help,
    )
