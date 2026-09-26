"""Streamlit エントリポイント."""

import streamlit as st

APP_TITLE = "月間シフト自動作成"


USAGE_STEPS = [
    "① スタッフ確認",
    "② 月間勤務条件入力",
    "③ 希望休入力",
    "④ 日別最低人数・ロール入力",
    "⑤ 事前チェック",
    "⑥ 自動シフト生成",
    "⑦ 管理者確認",
    "⑧ 手動修正",
    "⑨ 必要セルを固定",
    "⑩ 固定を残して再計算",
    "⑪ 確定",
    "⑫ Excel出力",
]


# 左メニュー（ファイル名ではなく日本語の表示名で並べる）
MENU_PAGES = [
    ("pages/01_staff.py", "① スタッフ管理"),
    ("pages/02_monthly_conditions.py", "② 月間勤務条件"),
    ("pages/03_preferences.py", "③ 希望休入力"),
    ("pages/04_requirements.py", "④ 日別最低人数"),
    ("pages/05_generate.py", "⑤ シフト生成"),
    ("pages/06_schedule.py", "⑥ シフト確認・確定"),
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
    st.write("ホテル客室清掃スタッフの月間勤務シフトを自動作成します。")
    st.caption("左のメニューから各画面へ移動してください。")

    st.subheader("使い方")
    for step in USAGE_STEPS:
        st.write(step)


if __name__ == "__main__":
    main()
