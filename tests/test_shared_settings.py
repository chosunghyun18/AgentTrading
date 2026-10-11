"""shared.settings — 우선순위(환경 → .env → 기본값), .env 비주입, 플래그 엄격 해석, 모드 별칭, 인증 정책, bind fail-closed,
ENV_ONLY 기본값이 `src/agent/settings.py` 와 같은지(드리프트 가드), 카탈로그 ↔ DEPLOY.md 일치."""

import os

import pytest

from src.agent import settings as agent_settings
from src.shared import settings as cfg


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


# 우선순위 ---------------------------------------------------------------------------------------------

def test_defaults_match_current_local_behaviour():
    assert cfg.get("AT_BIND_ADDRESS") == "127.0.0.1" and cfg.get("AT_PORT") == "8501"
    assert cfg.flag("AT_AUTH_REQUIRED") is False and cfg.flag("AT_AUTH_EXTERNAL") is False
    assert cfg.path("AT_VAULT_DIR") is None  # 볼트 연동은 설정했을 때만
    assert cfg.get("AT_SYMBOL") == "XBTUSD" and cfg.get("AT_TRADE_SYMBOL") == "BTCUSDT"
    assert cfg.path("AT_OUT_DIR") == cfg.ROOT / "data" / "out" / "backtest"
    assert cfg.path("AT_RISK_FILE") == cfg.ROOT / "config" / "risk.yaml"


def test_env_beats_dotenv_beats_default(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text('AT_PORT=9000\nAT_SYMBOL="ETHUSD"\n', encoding="utf-8")
    cfg.reload()
    assert cfg.get("AT_PORT") == "9000" and cfg.get("AT_SYMBOL") == "ETHUSD"
    monkeypatch.setenv("AT_PORT", " 9100 ")
    assert cfg.get("AT_PORT") == "9100"
    monkeypatch.setenv("AT_SYMBOL", "")  # 빈 값은 미설정 → .env 로 넘어간다
    assert cfg.get("AT_SYMBOL") == "ETHUSD"


def test_dotenv_is_read_not_injected(tmp_path):
    (tmp_path / ".env").write_text("AT_PORT=9000\nBYBIT_DEMO_API_KEY=k\n", encoding="utf-8")
    cfg.reload()
    assert cfg.get("AT_PORT") == "9000"
    assert "AT_PORT" not in os.environ and "BYBIT_DEMO_API_KEY" not in os.environ


def test_missing_dotenv_is_fine():
    assert cfg.get("AT_PORT") == "8501"


def test_unknown_name():
    with pytest.raises(KeyError):
        cfg.get("AT_NOPE")


# 플래그 — 엄격 해석 -----------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("YES", True), ("on", True),
                                          ("0", False), ("false", False), ("No", False), ("off", False)])
def test_flag_values(monkeypatch, raw, expected):
    monkeypatch.setenv("AT_AUTH_EXTERNAL", raw)
    assert cfg.flag("AT_AUTH_EXTERNAL") is expected


@pytest.mark.parametrize("raw", ["enabled", "y", "2", "required"])
def test_flag_rejects_ambiguous_values(monkeypatch, raw):
    monkeypatch.setenv("AT_AUTH_REQUIRED", raw)
    with pytest.raises(ValueError, match="알 수 없는 값"):
        cfg.flag("AT_AUTH_REQUIRED")
    with pytest.raises(ValueError):
        cfg.auth_policy()


# 모드 ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("demo", "paper"), ("paper", "paper"), ("live", "live"),
                                          ("shadow", "shadow"), ("backtest", "backtest")])
def test_normalize_mode(raw, expected):
    assert cfg.normalize_mode(raw) == expected


def test_normalize_mode_rejects_unknown():
    with pytest.raises(ValueError):
        cfg.normalize_mode("prod")


# 인증 정책·bind -----------------------------------------------------------------------------------------

def test_allowed_emails_normalized(monkeypatch):
    monkeypatch.setenv("AT_AUTH_ALLOWED_EMAILS", " A@x.io, b@y.io ,,")
    assert cfg.allowed_emails() == frozenset({"a@x.io", "b@y.io"})
    assert cfg.auth_policy() == cfg.AuthPolicy(False, False, frozenset({"a@x.io", "b@y.io"}))


@pytest.mark.parametrize("address", ["127.0.0.1", "127.0.0.2", "localhost", " LOCALHOST ", "::1", "[::1]",
                                     "0000:0000:0000:0000:0000:0000:0000:0001"])
def test_is_loopback(address):
    assert cfg.is_loopback(address) is True


@pytest.mark.parametrize("address", [None, "", "0.0.0.0", "::", "192.168.0.10", "example.com", "10.0.0.1"])
def test_is_not_loopback(address):
    assert cfg.is_loopback(address) is False


@pytest.mark.parametrize("address", ["127.0.0.1", "localhost", "::1"])
def test_loopback_without_auth_allowed(address):
    ok, why = cfg.bind_allowed(address, cfg.auth_policy())
    assert ok is True and "loopback" in why


@pytest.mark.parametrize("address", ["0.0.0.0", "", None, "192.168.0.10", "::"])
def test_external_without_auth_refused(address):
    ok, why = cfg.bind_allowed(address, cfg.auth_policy())
    assert ok is False and "인증" in why and "AT_AUTH_EXTERNAL=1" in why


def test_external_with_auth_and_allowlist():
    ok, why = cfg.bind_allowed("0.0.0.0", _policy(required=True, allowed={"a@x.io"}))
    assert ok is True and "1명" in why


def test_auth_required_without_allowlist_starts_but_nobody_passes():
    """재시작 루프 대신 기동은 하고, 화면이 설정 안내를 보인다(아무도 통과 못 함은 auth.decide 가 보장)."""
    ok, why = cfg.bind_allowed("0.0.0.0", _policy(required=True))
    assert ok is True and "아무도 통과" in why and "AT_AUTH_ALLOWED_EMAILS" in why


def test_external_auth_declared():
    ok, why = cfg.bind_allowed("0.0.0.0", _policy(external=True))
    assert ok is True and "AT_AUTH_EXTERNAL" in why


def test_required_wins_over_external_with_hint():
    ok, why = cfg.bind_allowed("0.0.0.0", _policy(required=True, external=True, allowed={"a@x.io"}))
    assert ok is True and "REQUIRED 가 우선" in why


# 카탈로그 -----------------------------------------------------------------------------------------------

def test_vars_catalogue_documented():
    for name, (_default, desc) in cfg.VARS.items():
        assert name.startswith("AT_") and desc
    assert cfg.ENV_ONLY <= set(cfg.VARS)


def test_env_only_defaults_match_agent_settings():
    """`src/agent/settings.py` 는 아직 환경변수만 읽는다 — 기본값이 카탈로그와 어긋나면 안 된다."""
    assert agent_settings.agent_dir() == cfg.path("AT_AGENT_DIR")
    assert agent_settings.risk_file() == cfg.path("AT_RISK_FILE")
    assert agent_settings.symbol() == cfg.get("AT_TRADE_SYMBOL")
    assert agent_settings.LIVE_FLAG == "AT_LIVE_ENABLED" and cfg.get("AT_LIVE_ENABLED") == "0"


def test_every_var_is_in_deploy_doc():
    text = (cfg.ROOT / "DEPLOY.md").read_text(encoding="utf-8")
    missing = [k for k in cfg.VARS if k not in text]
    assert not missing, missing
