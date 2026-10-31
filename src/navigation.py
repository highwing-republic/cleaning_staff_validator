"""アプリ名と画面名の定義（画面間の案内文で同じ呼び方を使うため）.

画面名をここに集約することで、「シフト生成」「勤務表調整」などの呼び方が
画面によってずれないようにする。詳細操作の画面には番号を付けない
（ホームのかんたん作成の①〜④と番号の意味が重ならないようにするため）。
"""

APP_TITLE = "清掃勤務表作成"
APP_SUBTITLE = (
    "スタッフの通常勤務条件・勤務希望・予約状況から清掃勤務表を作成します。"
)

# 画面名（案内文ではこの名前を「」で囲んで使う）
# ふだん使う画面（ホームの「勤務表をつくる」から4段階で進む）
PAGE_HOME = "ホーム・かんたん作成"
PAGE_STAFF = "スタッフ管理"
PAGE_PREFERENCES = "勤務希望"
PAGE_REQUIREMENTS = "予約・必要人数"
PAGE_GENERATE = "シフト生成"
PAGE_SCHEDULE = "勤務表調整"
PAGE_OUTPUT = "勤務表出力"


# 各画面の見出し（各ページはここから見出しを取る）
HEADING_STAFF = PAGE_STAFF
HEADING_PREFERENCES = PAGE_PREFERENCES
HEADING_REQUIREMENTS = PAGE_REQUIREMENTS
HEADING_GENERATE = PAGE_GENERATE
HEADING_SCHEDULE = PAGE_SCHEDULE
HEADING_OUTPUT = PAGE_OUTPUT

# 左メニュー: 若女将が使う順番に並べる（ファイル名の番号は変更しない）
MAIN_PAGES: list[tuple[str, str]] = [
    ("app_pages/01_staff.py", HEADING_STAFF),
    ("app_pages/05_preferences.py", HEADING_PREFERENCES),
    ("app_pages/03_requirements.py", HEADING_REQUIREMENTS),
    ("app_pages/06_generate.py", HEADING_GENERATE),
    ("app_pages/07_schedule.py", HEADING_SCHEDULE),
    ("app_pages/08_output.py", HEADING_OUTPUT),
]

# 勤怠CSVの取込・検証の画面は削除した（今の勤務表づくりには使わないため）
MENU_PAGES: list[tuple[str, str]] = list(MAIN_PAGES)

MAIN_SECTION_LABEL = "勤務表を作る"

# ホーム画面に出す作業の流れ
USAGE_STEPS: list[str] = [
    f"1. 「{PAGE_STAFF}」でスタッフと通常の勤務条件を確認する",
    f"2. 「{PAGE_PREFERENCES}」で紙に書かれた「普段と違う希望」を転記する",
    f"3. 「{PAGE_REQUIREMENTS}」で予約室数と必要人数を入力する",
    f"4. 「{PAGE_GENERATE}」で勤務案を作り、下書き保存する",
    f"5. 「{PAGE_SCHEDULE}」で手直しして、日ごとに確定する",
    f"6. 「{PAGE_OUTPUT}」でExcelを出力して印刷する",
]

# ホーム画面（関数で作るページ）を他の画面から開くため、app.py が毎回ここへ置く
HOME_PAGE_STATE_KEY = "_home_page"
