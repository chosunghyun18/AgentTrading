"""ui.auth — 판정 순수 함수 `decide` + `app.py` 게이트(AppTest): 인증 요구 시 로그인·설정 안내·거부, 기본값은 통과."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.shared import settings as cfg
from src.ui import auth

APP = Path(__file__).resolve().parents[1] / "src" / "ui" / "app.py"
SECRETS_AUTH = {"redirect_uri": "http://localhost:8501/oauth2callback", "cookie_secret": "x" * 32,
                "client_id": "id", "client_secret": "secret",
                "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration"}
TIMEOUT = 60


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for k in cfg.VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    cfg.reload()
    yield
    cfg.reload()


def _policy(required=False, external=False, allowed=()):
    return cfg.AuthPolicy(required, external, frozenset(allowed))


# decide() — 순수 판정 ---------------------------------------------------------------------------------

def test_default_allows_loopback_without_login():
    assert auth.decide("127.0.0.1", _policy(), None)[0] == auth.ALLOW


@pytest.mark.parametrize("address", ["0.0.0.0", None, ""])
def test_external_bind_without_auth_refused(address):
    verdict, why = auth.decide(address, _policy(), None)
    assert verdict == auth.REFUSE and "인증" in why


def test_external_bind_declared_allows_without_login():
    assert auth.decide("0.0.0.0", _policy(external=True), None)[0] == auth.ALLOW


def test_setup_when_auth_not_configured():
    verdict, why = auth.decide("127.0.0.1", _policy(True, allowed={"a@x.io"}), None)
    assert verdict == auth.SETUP and "secrets.toml" in why


def test_login_when_not_logged_in():
    assert auth.decide("127.0.0.1", _policy(True, allowed={"a@x.io"}), auth.User(False))[0] == auth.LOGIN


def test_deny_unverified_or_missing_email():
    pol = _policy(True, allowed={"a@x.io"})
    assert auth.decide("127.0.0.1", pol, auth.User(True, "a@x.io", verified=False))[0] == auth.DENY
    assert auth.decide("127.0.0.1", pol, auth.User(True, None, verified=True))[0] == auth.DENY


def test_deny_not_in_allowlist():
    verdict, why = auth.decide("127.0.0.1", _policy(True, allowed={"a@x.io"}), auth.User(True, "b@y.io", True))
    assert verdict == auth.DENY and "b@y.io" in why


def test_allow_listed_verified_user_even_on_external_bind():
    verdict, why = auth.decide("0.0.0.0", _policy(True, allowed={"a@x.io"}), auth.User(True, "a@x.io", True))
    assert verdict == auth.ALLOW and "a@x.io" in why


def test_auth_required_with_empty_allowlist_refuses_everyone():
    assert auth.decide("0.0.0.0", _policy(True), auth.User(True, "a@x.io", True))[0] == auth.REFUSE


# gate() — app.py 를 AppTest 로 실행 ---------------------------------------------------------------------

def _run(monkeypatch, env: dict, secrets: dict | None = None, user: auth.User | None = None) -> AppTest:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if user is not None:
        monkeypatch.setattr(auth, "current_user", lambda: user)
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT)
    if secrets is not None:
        at.secrets["auth"] = secrets
    return at.run()


def _no_navigation(at: AppTest) -> bool:
    """내비게이션이 돌지 않았다 — app.py 는 nav 뒤에 사이드바 캡션을 쓴다."""
    return not at.sidebar.caption


def test_gate_login_screen_when_auth_required(monkeypatch):
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "1", "AT_AUTH_ALLOWED_EMAILS": "a@x.io"}, secrets=SECRETS_AUTH)
    assert not at.exception
    assert [b.label for b in at.button] == ["로그인"]
    assert _no_navigation(at)


def test_gate_setup_notice_without_auth_secrets(monkeypatch):
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "1", "AT_AUTH_ALLOWED_EMAILS": "a@x.io"})
    assert not at.exception
    assert any("secrets.toml" in e.value for e in at.error)
    assert not at.button and _no_navigation(at)


def test_gate_denies_account_not_in_allowlist(monkeypatch):
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "1", "AT_AUTH_ALLOWED_EMAILS": "a@x.io"}, secrets=SECRETS_AUTH,
              user=auth.User(True, "b@y.io", True))
    assert not at.exception
    assert any("b@y.io" in e.value for e in at.error)
    assert [b.label for b in at.button] == ["로그아웃"]
    assert _no_navigation(at)


def test_gate_denies_everyone_when_allowlist_empty(monkeypatch):
    """loopback 에서는 기동은 되지만(외부 bind 가 아니므로) 허용 목록이 비어 있으면 누구도 본문을 못 본다."""
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "1"}, secrets=SECRETS_AUTH, user=auth.User(True, "a@x.io", True))
    assert not at.exception
    assert any("허용 목록" in e.value for e in at.error)
    assert [b.label for b in at.button] == ["로그아웃"]
    assert _no_navigation(at)


def test_current_user_without_secrets_is_setup():
    """AppTest 밖(bare 모드)에서는 secrets 가 없으므로 None(설정 필요)."""
    assert auth.current_user() is None
