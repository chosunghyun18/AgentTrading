"""ui.auth — secrets 검사·`current_user` 어댑터·판정 순수 함수 `decide` + `app.py` 게이트(AppTest):
기본값은 내비게이션까지 렌더, 인증 요구 시 로그인·설정 안내·거부, 외부 bind + 무인증은 거부."""

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 — pytest 가 tomli 를 끌어온다
    import tomli as tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.web import bootstrap

from src.agent import service, settings as agent_settings
from src.agent import strategies as sg
from src.agent.service import TradingService
from src.shared import settings as cfg
from src.ui import auth
from tests import ui_fixtures as fx
from tests.agent_fakes import FakeClient

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "ui" / "app.py"
VALID_AUTH = {"redirect_uri": "http://localhost:8501/oauth2callback", "cookie_secret": "x" * 64,
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


# validate_auth_secrets ---------------------------------------------------------------------------------

def test_valid_auth_section_passes():
    assert auth.validate_auth_secrets(VALID_AUTH) is None


def test_missing_section_and_keys():
    assert "[auth]" in auth.validate_auth_secrets(None)
    for k in auth.AUTH_KEYS:
        bad = {**VALID_AUTH, k: ""}
        assert k in auth.validate_auth_secrets(bad)


def test_short_cookie_secret_rejected():
    msg = auth.validate_auth_secrets({**VALID_AUTH, "cookie_secret": "short-secret"})
    assert msg and "cookie_secret" in msg and "token_hex" in msg


def test_example_secrets_file_is_rejected_as_is():
    """예시 파일을 그대로 복사하면 쿠키를 누구나 위조할 수 있다 — 반드시 SETUP 으로 막힌다."""
    section = tomllib.loads((ROOT / ".streamlit" / "secrets.example.toml").read_text(encoding="utf-8"))["auth"]
    assert auth.validate_auth_secrets(section) is not None


# current_user — st.secrets·st.user 어댑터(가짜 st) ----------------------------------------------------------

class _FakeUser(dict):
    def __init__(self, data: dict, logged_in):
        super().__init__(data)
        self._logged = logged_in

    @property
    def is_logged_in(self):
        if self._logged is None:
            raise AttributeError('st.user has no attribute "is_logged_in"')
        return self._logged


def _fake_st(monkeypatch, secrets: dict, user=None):
    monkeypatch.setattr(auth, "st", SimpleNamespace(secrets=secrets, user=user))


def test_current_user_none_without_auth_secrets(monkeypatch):
    _fake_st(monkeypatch, {}, _FakeUser({"email": "a@x.io", "email_verified": True}, True))
    assert auth.current_user() is None
    _fake_st(monkeypatch, {"auth": {**VALID_AUTH, "cookie_secret": "short"}}, _FakeUser({}, True))
    assert auth.current_user() is None


def test_current_user_not_logged_in_or_unknown(monkeypatch):
    _fake_st(monkeypatch, {"auth": VALID_AUTH}, _FakeUser({}, False))
    assert auth.current_user() == auth.User(False)
    _fake_st(monkeypatch, {"auth": VALID_AUTH}, _FakeUser({}, None))  # AppTest 처럼 속성이 없다
    assert auth.current_user() == auth.User(False)


def test_current_user_logged_in_normalizes_email_and_claim(monkeypatch):
    _fake_st(monkeypatch, {"auth": VALID_AUTH}, _FakeUser({"email": " A@X.io ", "email_verified": True}, True))
    assert auth.current_user() == auth.User(True, "a@x.io", True)


@pytest.mark.parametrize("claim,verified", [("false", False), ("False", False), (None, False), (0, False),
                                            ("true", True), ("TRUE", True), (True, True)])
def test_current_user_email_verified_is_not_fail_open(monkeypatch, claim, verified):
    data = {"email": "a@x.io"} | ({} if claim is None else {"email_verified": claim})
    _fake_st(monkeypatch, {"auth": VALID_AUTH}, _FakeUser(data, True))
    assert auth.current_user().verified is verified


# decide — 순수 판정 --------------------------------------------------------------------------------------

def test_default_allows_loopback_without_login():
    assert auth.decide("127.0.0.1", _policy(), None)[0] == auth.ALLOW


@pytest.mark.parametrize("address", ["0.0.0.0", None, ""])
def test_external_bind_without_auth_refused(address):
    verdict, why = auth.decide(address, _policy(), None)
    assert verdict == auth.REFUSE and "인증" in why


def test_external_bind_declared_allows_without_login():
    assert auth.decide("0.0.0.0", _policy(external=True), None)[0] == auth.ALLOW


def test_setup_when_secrets_issue():
    verdict, why = auth.decide("127.0.0.1", _policy(True, allowed={"a@x.io"}), None, secrets_issue="[auth] 절이 없다")
    assert verdict == auth.SETUP and "[auth]" in why


def test_setup_when_allowlist_empty_even_for_logged_in_user():
    verdict, why = auth.decide("0.0.0.0", _policy(True), auth.User(True, "a@x.io", True))
    assert verdict == auth.SETUP and "AT_AUTH_ALLOWED_EMAILS" in why


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


# gate — app.py 를 AppTest 로 실행 ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    return fx.build(tmp_path_factory.mktemp("ui-auth"))


@pytest.fixture
def app_env(tree, tmp_path, monkeypatch):
    """내비게이션이 실제로 돌 때(기본 페이지 = 전략) 거래소·실데이터·.env 를 건드리지 않게 하는 패치."""
    monkeypatch.setenv("AT_OUT_DIR", str(tree["out"]))
    monkeypatch.setenv("AT_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setattr(agent_settings, "load_env", lambda: None)
    monkeypatch.setattr(service, "FACTORY",
                        lambda mode: TradingService(FakeClient(), mode, tmp_path / "agent", tmp_path / "r.yaml"))
    reg = tuple(s if s.id != "v1" else sg.Strategy(**{**s.__dict__, "report": fx.REPORT}) for s in sg.REGISTRY)
    monkeypatch.setattr(sg, "REGISTRY", reg)
    monkeypatch.setattr(sg, "registry", lambda: reg)


def _run(monkeypatch, env: dict, secrets: dict | None = None, user: auth.User | None = None) -> AppTest:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if user is not None:
        monkeypatch.setattr(auth, "current_user", lambda: user)
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT)
    # 비어 있지 않은 at.secrets 는 로컬 secrets.toml 을 대신한다 → 테스트가 배포자의 로컬 OIDC 설정에 영향받지 않는다
    at.secrets["other"] = {"isolated": "1"}
    if secrets is not None:
        at.secrets["auth"] = secrets
    return at.run()


def _no_navigation(at: AppTest) -> bool:
    """내비게이션이 돌지 않았다 — app.py 는 nav 뒤에 사이드바 캡션을 쓴다."""
    return not at.sidebar.caption


def _buttons(at: AppTest) -> list[str]:
    return [b.label for b in at.button]


def test_gate_default_renders_navigation(monkeypatch, app_env):
    at = _run(monkeypatch, {})
    assert not at.exception, [e.value for e in at.exception]
    assert at.sidebar.caption and "로그인" not in _buttons(at)


def test_gate_allowed_user_renders_navigation(monkeypatch, app_env):
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "1", "AT_AUTH_ALLOWED_EMAILS": "a@x.io"}, secrets=VALID_AUTH,
              user=auth.User(True, "a@x.io", True))
    assert not at.exception, [e.value for e in at.exception]
    assert at.sidebar.caption and not at.error


def test_gate_login_screen_when_auth_required(monkeypatch):
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "1", "AT_AUTH_ALLOWED_EMAILS": "a@x.io"}, secrets=VALID_AUTH)
    assert not at.exception
    assert _buttons(at) == ["로그인"] and _no_navigation(at)


def test_gate_setup_notice_without_auth_section(monkeypatch):
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "1", "AT_AUTH_ALLOWED_EMAILS": "a@x.io"})
    assert not at.exception
    assert any("[auth]" in e.value for e in at.error)
    assert not at.button and _no_navigation(at)


def test_gate_setup_notice_with_weak_cookie_secret(monkeypatch):
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "1", "AT_AUTH_ALLOWED_EMAILS": "a@x.io"},
              secrets={**VALID_AUTH, "cookie_secret": "short"}, user=auth.User(True, "a@x.io", True))
    assert not at.exception
    assert any("cookie_secret" in e.value for e in at.error)
    assert not at.button and _no_navigation(at)


def test_gate_denies_account_not_in_allowlist(monkeypatch):
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "1", "AT_AUTH_ALLOWED_EMAILS": "a@x.io"}, secrets=VALID_AUTH,
              user=auth.User(True, "b@y.io", True))
    assert not at.exception
    assert any("b@y.io" in e.value for e in at.error)
    assert _buttons(at) == ["로그아웃"] and _no_navigation(at)


def test_gate_setup_when_allowlist_empty(monkeypatch):
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "1"}, secrets=VALID_AUTH, user=auth.User(True, "a@x.io", True))
    assert not at.exception
    assert any("AT_AUTH_ALLOWED_EMAILS" in e.value for e in at.error)
    assert not at.button and _no_navigation(at)


def test_gate_refuses_external_bind_without_auth(monkeypatch):
    """`gate()` 가 실제 server.address 를 본다 — 0.0.0.0 + 무인증이면 본문을 그리지 않는다."""
    bootstrap.load_config_options({"server.address": "0.0.0.0"})
    try:
        at = _run(monkeypatch, {})
    finally:
        bootstrap.load_config_options({})  # 설정 파일 값(레포 config.toml = 127.0.0.1)으로 복원
    assert not at.exception
    assert any("loopback 이 아닌데" in e.value for e in at.error)
    assert not at.button and _no_navigation(at)


def test_gate_rejects_ambiguous_flag(monkeypatch):
    at = _run(monkeypatch, {"AT_AUTH_REQUIRED": "enabled"})
    assert not at.exception
    assert any("알 수 없는 값" in e.value for e in at.error) and _no_navigation(at)


def test_oidc_client_importable_when_auth_extra_installed():
    """`uv sync --extra auth` 환경에서 st.login 이 쓰는 Authlib starlette 클라이언트가 import 돼야 한다(httpx 포함)."""
    pytest.importorskip("authlib")
    import authlib.integrations.starlette_client  # noqa: F401
