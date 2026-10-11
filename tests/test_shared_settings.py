"""shared.settings — 우선순위(환경 → .env → 기본값), .env 비주입, 모드 별칭, 인증 정책, bind fail-closed."""

import os

import pytest

from src.shared import settings as cfg


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for k in cfg.VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    cfg.reload()
    yield
    cfg.reload()


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
    (tmp_path / ".env").write_text("AT_PORT=9000\n", encoding="utf-8")
    cfg.reload()
    assert cfg.get("AT_PORT") == "9000"
    assert "AT_PORT" not in os.environ


def test_missing_dotenv_is_fine():
    assert cfg.get("AT_PORT") == "8501"


def test_unknown_name():
    with pytest.raises(KeyError):
        cfg.get("AT_NOPE")


@pytest.mark.parametrize("raw,expected", [("demo", "paper"), ("paper", "paper"), ("live", "live"),
                                          ("shadow", "shadow"), ("backtest", "backtest")])
def test_normalize_mode(raw, expected):
    assert cfg.normalize_mode(raw) == expected


def test_normalize_mode_rejects_unknown():
    with pytest.raises(ValueError):
        cfg.normalize_mode("prod")


def test_allowed_emails_normalized(monkeypatch):
    monkeypatch.setenv("AT_AUTH_ALLOWED_EMAILS", " A@x.io, b@y.io ,,")
    assert cfg.allowed_emails() == frozenset({"a@x.io", "b@y.io"})
    assert cfg.auth_policy() == cfg.AuthPolicy(False, False, frozenset({"a@x.io", "b@y.io"}))


@pytest.mark.parametrize("address", ["127.0.0.1", "localhost", "::1", " LOCALHOST "])
def test_loopback_without_auth_allowed(address):
    ok, why = cfg.bind_allowed(address, cfg.auth_policy())
    assert ok is True and "loopback" in why


@pytest.mark.parametrize("address", ["0.0.0.0", "", None, "192.168.0.10", "::"])
def test_external_without_auth_refused(address):
    ok, why = cfg.bind_allowed(address, cfg.auth_policy())
    assert ok is False and "인증" in why


def test_external_with_auth_and_allowlist(monkeypatch):
    monkeypatch.setenv("AT_AUTH_REQUIRED", "1")
    monkeypatch.setenv("AT_AUTH_ALLOWED_EMAILS", "a@x.io")
    ok, why = cfg.bind_allowed("0.0.0.0", cfg.auth_policy())
    assert ok is True and "1명" in why


def test_auth_required_without_allowlist_refused(monkeypatch):
    monkeypatch.setenv("AT_AUTH_REQUIRED", "1")
    ok, why = cfg.bind_allowed("0.0.0.0", cfg.auth_policy())
    assert ok is False and "AT_AUTH_ALLOWED_EMAILS" in why


def test_external_auth_declared(monkeypatch):
    monkeypatch.setenv("AT_AUTH_EXTERNAL", "yes")
    ok, why = cfg.bind_allowed("0.0.0.0", cfg.auth_policy())
    assert ok is True and "AT_AUTH_EXTERNAL" in why


def test_vars_catalogue_documented():
    for name, (_default, desc) in cfg.VARS.items():
        assert name.startswith("AT_") and desc
