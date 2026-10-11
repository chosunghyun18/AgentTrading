"""관리 화면 접근 게이트 — 인증(OIDC `st.login`) + 인가(허용 이메일) + 외부 bind fail-closed.

기본값(환경변수 없음)은 지금과 같다: 127.0.0.1 에서 인증 없이 연다.
- `AT_AUTH_REQUIRED=1`: 로그인 + `AT_AUTH_ALLOWED_EMAILS` 에 있는 확인된 이메일만 본문을 본다. Streamlit 은 로그인 여부로
  페이지를 자동으로 막아 주지 않으므로 `app.py` 가 내비게이션 전에 `gate()` 를 부른다.
- bind 주소가 loopback 이 아닌데 인증 요구도 외부 인증 선언(`AT_AUTH_EXTERNAL=1`)도 없으면 본문을 그리지 않는다
  (런처 `python -m src.ui.serve` 는 기동 자체를 거부한다 — 이중 방어).
- `st.login` 전제: `streamlit[auth]`(Authlib + httpx, `uv sync --extra auth`) + `.streamlit/secrets.toml` 의 `[auth]`
  (redirect_uri·cookie_secret·client_id·client_secret·server_metadata_url). `[auth]` 가 없거나 `cookie_secret` 이 짧으면
  설정 안내만 보이고 멈춘다 — 예시 파일의 값을 그대로 쓰면 쿠키를 누구나 위조할 수 있어서다.
판정은 순수 함수 `decide()`·`validate_auth_secrets()` 에 모아 테스트하고, Streamlit 호출은 `secrets_problem()`·
`current_user()`·`gate()` 에만 둔다. 설계: Obsidian `design/platform-architecture.md` 3.1절 "인증·인가".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import streamlit as st

from src.shared import settings

ALLOW, LOGIN, DENY, REFUSE, SETUP = "allow", "login", "deny", "refuse", "setup"
AUTH_KEYS = ("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url")
MIN_COOKIE_SECRET = 32
COOKIE_SECRET_HINT = "python -c \"import secrets; print(secrets.token_hex(32))\" 로 만든다"


@dataclass(frozen=True)
class User:
    logged_in: bool
    email: str | None = None   # 소문자
    verified: bool = False


def validate_auth_secrets(section: Mapping | None) -> str | None:
    """`[auth]` 절 검사. 문제가 없으면 None, 있으면 사람이 읽는 사유."""
    if section is None:
        return "`.streamlit/secrets.toml` 에 `[auth]` 절이 없다 — `secrets.example.toml` 을 복사해 채운다"
    for k in AUTH_KEYS:
        if not str(section.get(k) or "").strip():
            return f"`[auth]` 의 `{k}` 가 비어 있다"
    if len(str(section.get("cookie_secret"))) < MIN_COOKIE_SECRET:
        return f"`[auth]` 의 `cookie_secret` 이 {MIN_COOKIE_SECRET}자 미만이다 — {COOKIE_SECRET_HINT}"
    return None


def secrets_problem() -> str | None:
    """실행 중인 Streamlit 의 secrets 로 `[auth]` 를 검사한다. secrets 파일이 아예 없으면 '절이 없다'."""
    try:
        section = st.secrets["auth"] if "auth" in st.secrets else None
    except Exception:  # noqa: BLE001 — StreamlitSecretNotFoundError 등: 설정 없음으로 본다
        section = None
    return validate_auth_secrets(section)


def _truthy_claim(v) -> bool:
    """`email_verified` claim — bool True 또는 문자열 "true" 만 참. 문자열 "false"·None·누락은 거짓."""
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


def current_user() -> User | None:
    """`st.user` 를 읽는다. `[auth]` 가 없거나 불완전하면 None. 로그인 상태를 알 수 없으면(AppTest 등) 비로그인."""
    if secrets_problem():
        return None
    try:
        logged = bool(st.user.is_logged_in)
    except Exception:  # noqa: BLE001 — 테스트 러너·인증 런타임 없음: 비로그인으로 본다
        return User(False)
    if not logged:
        return User(False)
    email = st.user.get("email")
    return User(True, email=str(email).strip().lower() if email else None,
                verified=_truthy_claim(st.user.get("email_verified")))


def decide(address: str | None, policy: settings.AuthPolicy, user: User | None,
           secrets_issue: str | None = None) -> tuple[str, str]:
    """(판정, 사유). 판정은 allow·login·deny·refuse·setup 중 하나. 인증 요구가 없으면 bind 검사만 한다."""
    ok, why = settings.bind_allowed(address, policy)
    if not ok:
        return REFUSE, why
    if not policy.required:
        return ALLOW, why
    if secrets_issue:
        return SETUP, secrets_issue
    if not policy.allowed:
        return SETUP, "AT_AUTH_ALLOWED_EMAILS 가 비어 있다 — 허용 이메일을 넣기 전에는 아무도 들어올 수 없다"
    if user is None:
        return SETUP, "`[auth]` 설정을 읽을 수 없다"
    if not user.logged_in:
        return LOGIN, "로그인 필요"
    if not user.email or not user.verified:
        return DENY, "확인된 이메일이 없는 계정"
    if user.email not in policy.allowed:
        return DENY, f"허용 목록에 없는 계정: {user.email}"
    return ALLOW, f"허용된 계정: {user.email}"


def gate() -> None:
    """허용이 아니면 안내만 그리고 `st.stop()`. 인증 요구가 없고 loopback 이면 아무것도 하지 않는다."""
    try:
        policy = settings.auth_policy()
    except ValueError as e:
        st.title("AgentTrading")
        st.error(f"설정 오류 — {e}")
        st.stop()
    address = st.get_option("server.address")
    issue = secrets_problem() if policy.required else None
    user = current_user() if policy.required and not issue else None
    verdict, why = decide(address, policy, user, issue)
    if verdict == ALLOW:
        return
    st.title("AgentTrading")
    if verdict == LOGIN:
        st.info("관리 화면은 로그인한 허용 계정만 볼 수 있다.")
        st.button("로그인", type="primary", on_click=st.login, key="auth-login")
    elif verdict == DENY:
        st.error(f"접근 거부 — {why}")
        st.button("로그아웃", on_click=st.logout, key="auth-logout")
    elif verdict == SETUP:
        st.error(f"인증 설정이 필요하다 — {why}. 자세한 절차는 DEPLOY.md 3절.")
    else:  # REFUSE
        st.error(f"기동 조건 미충족 — {why}")
    st.stop()
