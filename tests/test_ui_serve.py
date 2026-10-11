"""ui.serve — 기동 런처: 외부 bind + 인증 없음 → 종료코드 2(exec 없음), 허용이면 streamlit exec 인자."""

import io

import pytest

from src.shared import settings as cfg
from src.ui import serve


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for k in cfg.VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    cfg.reload()
    yield
    cfg.reload()


def _main(argv):
    calls, err = [], io.StringIO()
    rc = serve.main(argv, execv=lambda prog, args: calls.append((prog, args)), err=err)
    return rc, calls, err.getvalue()


def test_default_execs_streamlit_on_loopback():
    rc, calls, msg = _main([])
    assert rc == 0 and len(calls) == 1 and "loopback" in msg
    prog, args = calls[0]
    assert prog == args[0]
    assert args[1:4] == ["-m", "streamlit", "run"] and args[4] == str(serve.APP)
    assert args[args.index("--server.address") + 1] == "127.0.0.1"
    assert args[args.index("--server.port") + 1] == "8501"
    assert "--server.headless" in args and args[args.index("--server.headless") + 1] == "true"
    assert args[args.index("--browser.gatherUsageStats") + 1] == "false"


def test_external_without_auth_refused():
    rc, calls, msg = _main(["--address", "0.0.0.0"])
    assert rc == serve.EXIT_REFUSED == 2 and calls == [] and "기동 거부" in msg


def test_env_address_and_port_with_external_auth(monkeypatch):
    monkeypatch.setenv("AT_BIND_ADDRESS", "0.0.0.0")
    monkeypatch.setenv("AT_PORT", "8600")
    monkeypatch.setenv("AT_AUTH_EXTERNAL", "1")
    rc, calls, _ = _main([])
    assert rc == 0
    args = calls[0][1]
    assert args[args.index("--server.address") + 1] == "0.0.0.0"
    assert args[args.index("--server.port") + 1] == "8600"


def test_auth_required_needs_allowlist(monkeypatch):
    monkeypatch.setenv("AT_BIND_ADDRESS", "0.0.0.0")
    monkeypatch.setenv("AT_AUTH_REQUIRED", "1")
    rc, calls, msg = _main([])
    assert rc == 2 and calls == [] and "AT_AUTH_ALLOWED_EMAILS" in msg
    monkeypatch.setenv("AT_AUTH_ALLOWED_EMAILS", "a@x.io")
    rc, calls, _ = _main([])
    assert rc == 0 and len(calls) == 1


def test_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("AT_BIND_ADDRESS", "0.0.0.0")
    rc, calls, _ = _main(["--address", "127.0.0.1", "--port", "9999"])
    assert rc == 0
    args = calls[0][1]
    assert args[args.index("--server.address") + 1] == "127.0.0.1"
    assert args[args.index("--server.port") + 1] == "9999"
