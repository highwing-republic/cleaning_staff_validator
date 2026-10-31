"""閲覧パスワード（公開URLでスタッフの情報を見られないようにする）.

Streamlit Cloud の URL は知っている人なら誰でも開けるため、Secrets にパスワードを
設定したときだけ、最初にパスワードを聞く。設定が無い環境（PC版・テスト）では何もしない。

Secrets（Streamlit Cloud の Settings → Secrets）の書き方:

    [access]
    password = "合言葉"

パスワードはブラウザのタブごと（Streamlit のセッションごと）に1回入れる。
パスワードを変えると、入れ直すまで開けなくなる（古いパスワードで開いていた画面も含む）。
"""

import hashlib
import hmac
import os
import time

import streamlit as st

SECRETS_SECTION = "access"
SECRETS_KEY = "password"
ENV_KEY = "CLEANING_STAFF_APP_PASSWORD"  # Secrets を使わない環境向け（任意）
STATE_KEY = "_access_granted"
FAILURE_DELAY_SECONDS = 1.0  # 総当たりを遅くする


def configured_password() -> str | None:
    """設定されたパスワード. 未設定なら None（パスワードなしで開く）."""
    env = os.environ.get(ENV_KEY, "").strip()
    if env:
        return env
    try:
        section = st.secrets.get(SECRETS_SECTION)
    except Exception:  # Secrets ファイルが無い環境では例外になる
        return None
    if not section:
        return None
    try:
        value = section.get(SECRETS_KEY)
    except AttributeError:
        return None
    value = str(value).strip() if value is not None else ""
    return value or None


def _fingerprint(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def is_authorized() -> bool:
    """この画面を開いてよいか（パスワード未設定なら常に True）."""
    password = configured_password()
    if password is None:
        return True
    return st.session_state.get(STATE_KEY) == _fingerprint(password)


def check_password(entered: str, password: str) -> bool:
    return hmac.compare_digest(entered.encode("utf-8"), password.encode("utf-8"))


def login_page(title: str) -> None:
    """パスワード入力画面."""
    st.title(title)
    st.write("この勤務表は関係者だけが見られます。パスワードを入力してください。")
    with st.form("access_login"):
        entered = st.text_input("パスワード", type="password", key="access_password_input")
        submitted = st.form_submit_button("開く", type="primary")
    if not submitted:
        return
    password = configured_password()
    if password is None or check_password(entered or "", password):
        if password is not None:
            st.session_state[STATE_KEY] = _fingerprint(password)
        st.rerun()
    time.sleep(FAILURE_DELAY_SECONDS)
    st.error("パスワードが違います。")
