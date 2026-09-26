"""Streamlit エントリポイント."""

import streamlit as st

APP_TITLE = "清掃人員シフト検証"


USAGE_STEPS = [
    "① スタッフ管理（従業員番号・ロール・スキル）",
    "② 勤怠CSV取込",
    "③ 日別必要人数・ロール・スキル条件の入力",
    "④ 日別検証結果の確認・Excel出力",
]


# 左メニュー（ファイル名ではなく日本語の表示名で並べる）
MENU_PAGES = [
    ("pages/01_staff.py", "① スタッフ管理"),
    ("pages/02_attendance_import.py", "② 勤怠CSV取込"),
    ("pages/03_requirements.py", "③ 日別必要条件"),
    ("pages/04_validation.py", "④ 日別検証"),
]


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    navigation = st.navigation(
        [st.Page(home, title="ホーム", default=True)]
        + [st.Page(path, title=title) for path, title in MENU_PAGES]
    )
    navigation.run()


def home() -> None:
    st.title(APP_TITLE)
    st.write(
        "勤怠システムから出力した月間勤務シフトCSVを読み込み、"
        "清掃部門の人員数・ロール・スキル構成を日別に検証します。"
    )
    st.caption("左のメニューから各画面へ移動してください。")

    st.subheader("使い方")
    for step in USAGE_STEPS:
        st.write(step)


if __name__ == "__main__":
    main()
