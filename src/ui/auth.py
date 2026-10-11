"""관리 화면 접근 게이트 — 인증(OIDC `st.login`) + 인가(허용 이메일) + 외부 bind fail-closed.

기본값(환경변수 없음)은 지금과 같다: 127.0.0.1 에서 인증 없이 연다.
- `AT_AUTH_REQUIRED=1`: 로그인 + `AT_AUTH_ALLOWED_EMAILS` 에 있는 확인된 이메일만 본문을 본다. Streamlit 은 로그인 여부로
  페이지를 자동으로 막아 주지 않으므로 `app.py` 가 내비게이션 전에 `gate()` 를 부른다.
- bind 주소가 loopback 이 아닌데 인증 요구도 외부 인증 선언(`AT_AUTH_EXTERNAL=1`)도 없으면 본문을 그리지 않는다
  (런처 `python -m src.ui.serve` 는 기동 자체를 거부한다 — 이중 방어).
- `st.login` 전제: `Authlib` 설치(`uv sync --extra auth`) + `.streamlit/secrets.toml` 의 `[auth]`(redirect_uri·
  cookie_secret·client_id·client_secret·server_metadata_url). `[auth]` 가 없으면 설정 안내만 보이고 멈춘다.
판정은 순수 함수 `decide()` 에 모아 테스트하고, Streamlit 호출은 `current_user()`·`gate()` 에만 둔다.
설계: Obsidian `design/platform-architecture.md` 3.1절 "인증·인가".
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from src.shared import settings

ALLOW, LOGIN, DENY, REFUSE, SETUP = "allow", "login", "deny", "refuse", "setup"


@dataclass(frozen=True)
class User:
    logged_in: bool
    email: str | None = None   # 소문자
    verified: bool = False


def auth_configured() -> bool:
    """`secrets.toml` 에 `[auth]` 가 있는가. secrets 파일이 아예 없으면 False."""
    try:
        return "auth" in st.secrets
    except Exception:  # noqa: BLE001 — StreamlitSecretNotFoundError 등: 설정 없음으로 본다
        return False


def current_user() -> User | None:
    """`st.user` 를 읽는다. `[auth]` 미설정이면 None. 로그인 상태를 알 수 없으면(AppTest 등) 비로그인."""
    if not auth_configured():
        return None
    try:
        logged = bool(st.user.is_logged_in)
    except Exception:  # noqa: BLE001 — 테스트 러너·인증 런타임 없음: 비로그인으로 본다
        return User(False)
    if not logged:
        return User(False)
    email = st.user.get("email")
    return User(True, email=str(email).strip().lower() if email else None,
                verified=bool(st.user.get("email_verified", False)))


def decide(address: str | None, policy: settings.AuthPolicy, user: User | None) -> tuple[str, str]:
    """(판정, 사유). 판정은 allow·login·deny·refuse·setup 중 하나."""
    ok, why = settings.bind_allowed(address, policy)
    if not ok:
        return REFUSE, why
    if not policy.required:
        return ALLOW, why
    if user is None:
        return SETUP, "AT_AUTH_REQUIRED=1 인데 `.streamlit/secrets.toml` 에 `[auth]` 가 없다"
    if not user.logged_in:
        return LOGIN, "로그인 필요"
    if not user.email or not user.verified:
        return DENY, "확인된 이메일이 없는 계정"
    if user.email not in policy.allowed:
        return DENY, f"허용 목록에 없는 계정: {user.email}"
    return ALLOW, f"허용된 계정: {user.email}"


def gate() -> None:
    """허용이 아니면 안내만 그리고 `st.stop()`. 인증 요구가 없고 loopback 이면 아무것도 하지 않는다."""
    policy = settings.auth_policy()
    address = st.get_option("server.address")
    verdict, why = decide(address, policy, current_user() if policy.required else None)
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
        st.error(f"인증 설정이 없다 — {why}. `.streamlit/secrets.example.toml` 을 복사해 채운다. "
                 "Authlib 도 필요하다(`uv sync --extra auth`).")
    else:  # REFUSE
        st.error(f"기동 조건 미충족 — {why}")
    st.stop()
