"""Local AppTest interactions for account drawdown; never contact an exchange."""

import pytest

from tests.test_ui_trading_pages import world, _run, _text


def test_save_80_without_keys(world):
    client = world["clients"]["demo"]
    client.has_keys = False
    at = _run("console_risk.py")
    assert at.number_input(key="dd-limit").value == 30.0
    at.number_input(key="dd-limit").set_value(80.0)
    at.button(key="dd-save").click().run()
    assert not at.exception
    from src.agent import service
    assert service.make_service().drawdown_status().get("limit_fraction") == .8
    assert at.button(key="dd-init").disabled
    assert not [c for c in client.calls if c[0] == "wallet"]


def _svc():
    from src.agent import service
    return service.make_service()


def _confirm(at, key):
    at.checkbox(key=key + "-ok").check().run()
    at.button(key=key).click().run()
    assert not at.exception
    assert not at.checkbox(key=key + "-ok").value


def _enroll(world, at):
    assert at.button(key="dd-init").disabled
    _confirm(at, "dd-init")
    assert _svc().drawdown_status()["enrolled"]


def test_exact_boundary_protected_orders_rebase_separate_resume(world):
    c = world["clients"]["demo"]
    c.equity = 50
    at = _run("console_risk.py")
    at.number_input(key="dd-limit").set_value(80.0)
    at.button(key="dd-save").click().run()
    _enroll(world, at)
    c.equity = 100
    at.button(key="dd-poll").click().run()
    assert _svc().drawdown_status()["peak_equity"] == 100
    c.orders = [
        {"orderId": "entry", "symbol": "BTCUSDT", "orderLinkId": "atg-entry", "reduceOnly": False},
        {"orderId": "protect", "symbol": "BTCUSDT", "orderLinkId": "atc-protect", "reduceOnly": True},
        {"orderId": "close", "symbol": "BTCUSDT", "orderLinkId": "atg-close", "closeOnTrigger": True},
        {"orderId": "manual", "symbol": "BTCUSDT", "orderLinkId": "manual-entry"},
    ]
    c.equity = 20
    at.button(key="dd-poll").click().run()
    dd = _svc().drawdown_status()
    assert dd["latched"] and dd["blocked"] and dd["drawdown"] == .8 and dd["max_drawdown"] == .8
    assert [o["orderId"] for o in c.orders] == ["protect", "close", "manual"]
    assert not [call for call in c.calls if call[0] in ("create_order", "cancel_all")]
    assert "80.00%" in _text(at.metric)
    assert "UTC" in _text(at.caption) and "latched" in _text(at.error)
    # Raising the policy does not clear the latch; restore 80 for blocked resume.
    at.number_input(key="dd-limit").set_value(90.0)
    at.button(key="dd-save").click().run()
    assert _svc().drawdown_status()["latched"]
    at.number_input(key="dd-limit").set_value(80.0)
    at.button(key="dd-save").click().run()
    _confirm(at, "dd-resume")
    assert "낙폭 보호 재개 실패" in _text(at.error)
    assert _svc().drawdown_status()["latched"]
    assert at.button(key="dd-rebase").disabled
    at.text_input(key="dd-reason").set_value("cashflow reconciliation").run()
    _confirm(at, "dd-rebase")  # unknown/manual entry must prevent rebase
    assert "재설정 실패" in _text(at.error)
    c.orders = [o for o in c.orders if o["orderId"] != "manual"]
    _confirm(at, "dd-rebase")
    dd = _svc().drawdown_status()
    assert dd["latched"] and dd["blocked"] and dd["drawdown"] == 0
    assert dd["history"][0]["max_drawdown"] == .8
    _confirm(at, "dd-resume")
    assert not _svc().drawdown_status()["latched"]
    assert not _svc().drawdown_status()["blocked"]


@pytest.mark.parametrize("percent", [0.0, 100.0])
def test_invalid_percent_preserves_policy(world, percent):
    at = _run("console_risk.py")
    at.number_input(key="dd-limit").set_value(percent)
    at.button(key="dd-save").click().run()
    assert not at.exception and "0% 초과 100% 미만" in _text(at.error)
    assert _svc().drawdown_status()["limit_fraction"] == .3


@pytest.mark.parametrize("problem", ["uid", "wallet", "malformed", "nonfinite"])
def test_enrollment_failure_shown_without_crash(world, problem):
    from src.agent.bybit import BybitError
    c = world["clients"]["demo"]
    if problem == "uid":
        c.uid = None
    elif problem == "wallet":
        c.fail["wallet"] = BybitError(10002, "offline fixture", "/v5/account/wallet-balance")
    elif problem == "malformed":
        c.equity = "bad equity"
    else:
        c.equity = float("nan")
    at = _run("console_risk.py")
    _confirm(at, "dd-init")
    assert "계정 낙폭 초기화 실패" in _text(at.error)
    assert not _svc().drawdown_status()["enrolled"]


def test_poll_query_failure_visible_and_cancel_failure_visible(world):
    from src.agent.bybit import BybitError
    c = world["clients"]["demo"]
    at = _run("console_risk.py")
    _enroll(world, at)
    c.fail["wallet"] = BybitError(10002, "offline fixture", "/v5/account/wallet-balance")
    at.button(key="dd-poll").click().run()
    assert not at.exception and "낙폭 관측 실패" in _text(at.error)
    at.run()  # flash consumed, failed poll must still block the status display
    assert "drawdown observation failed" in _text(at.error)
    assert "낙폭 검사상 신규 진입 허용" not in _text(at.success)
    del c.fail["wallet"]
    c.orders = [{"orderId": "entry", "symbol": "BTCUSDT", "orderLinkId": "atc-entry"}]
    c.fail["cancel_order"] = BybitError(10002, "cancel fixture", "/v5/order/cancel")
    c.equity = 100
    at.button(key="dd-poll").click().run()
    assert not at.exception and "취소 실패" in _text(at.error)
    assert _svc().drawdown_status()["latched"]


def test_partial_status_uses_safe_defaults(world, monkeypatch):
    from src.agent.service import TradingService
    monkeypatch.setattr(TradingService, "drawdown_status", lambda self: {"limit_fraction": None})
    at = _run("console_risk.py")
    assert at.number_input(key="dd-limit").value == 30
    assert "미관측" in _text(at.metric)


def test_unknown_order_shows_recorded_link_and_safe_cli_recovery(world):
    from src.agent.service import OrderUnknown
    from tests.test_agent_service import LIM, REQ
    svc = _svc()
    svc.save_limits(LIM)
    svc.initialize_drawdown(confirm=True)
    world["clients"]["demo"].fail["create_order"] = TimeoutError("fixture")
    with pytest.raises(OrderUnknown) as error:
        svc.place_order(REQ)
    at = _run("console_risk.py")
    assert error.value.link_id in _text(at.caption)
    assert "대조" in _text(at.warning)
    assert "reconcile" in _text(at.code)
    assert svc.drawdown_status()["latched"]
