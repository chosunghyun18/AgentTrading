"""agent.service — 모의 거래소로 주문·거부·청산·킬스위치·손익·키 점검."""

import pytest

from src.agent import risk
from src.agent.bybit import BybitError
from src.agent.risk import OrderRequest, RiskLimits
from src.agent.service import RiskRejected, TradingService
from tests.agent_fakes import FakeClient

LIM = RiskLimits(max_leverage=3, max_position_usdt=5_000, max_loss_per_trade_usdt=50, max_daily_loss_usdt=100)
REQ = OrderRequest(side="Buy", qty=0.0129, order_type="Market", leverage=2, stop_loss=79_600.04, take_profit=81_000)


@pytest.fixture
def svc(tmp_path):
    s = TradingService(FakeClient(), "demo", tmp_path / "agent", tmp_path / "risk.yaml", live_unlocked=lambda: False)
    s.save_limits(LIM)
    return s


def _events(s):
    return [r["event"] for r in risk.read_audit(s.state_dir)][::-1]


def test_place_order_normalizes_and_sets_stop(svc):
    svc.place_order(REQ)
    names = [c[0] for c in svc.client.calls if c[0] in ("set_leverage", "create_order")]
    assert names == ["set_leverage", "create_order"]
    p = [c for c in svc.client.calls if c[0] == "create_order"][0][1]
    assert p["qty"] == "0.012" and p["stopLoss"] == "79600" and p["slTriggerBy"] == "MarkPrice"
    assert p["takeProfit"] == "81000" and p["positionIdx"] == 0 and p["orderLinkId"].startswith("atc-")
    assert svc.position_qty(svc.position()) == pytest.approx(0.012)
    assert _events(svc)[-1] == "order_placed"


def test_rejected_order_never_reaches_exchange(svc):
    with pytest.raises(RiskRejected) as e:
        svc.place_order(OrderRequest("Buy", 0.01, "Market", 2, stop_loss=None))
    assert "손절가 필수" in str(e.value)
    assert all(c[0] != "create_order" for c in svc.client.calls)
    assert _events(svc)[-1] == "order_rejected"


def test_no_limits_blocks(tmp_path):
    s = TradingService(FakeClient(), "demo", tmp_path / "a", tmp_path / "none.yaml", live_unlocked=lambda: False)
    with pytest.raises(RiskRejected, match="한도 미설정"):
        s.place_order(REQ)


def test_live_mode_requires_unlock(tmp_path):
    s = TradingService(FakeClient(), "live", tmp_path / "a", tmp_path / "r.yaml", live_unlocked=lambda: False)
    s.save_limits(LIM)
    with pytest.raises(RiskRejected, match="LIVE 잠금"):
        s.place_order(REQ)


def test_daily_loss_includes_realized_and_unrealized(svc):
    svc.client.closed = [{"closedPnl": "-70"}, {"closedPnl": "-20"}]
    svc.client.pos.update(side="Buy", size="0.01", unrealisedPnl="-15")
    assert svc.today_pnl(svc.position()) == pytest.approx(-105)
    with pytest.raises(RiskRejected, match="일 손실"):
        svc.place_order(REQ)


def test_limit_order_and_cancel(svc):
    svc.place_order(OrderRequest("Buy", 0.01, "Limit", 2, stop_loss=78_000, price=79_000.04))
    o = svc.client.orders[0]
    assert o["price"] == "79000" and o["timeInForce"] == "GTC"
    svc.cancel_order(o["orderId"])
    assert svc.client.orders == []


def test_close_position_reduce_only(svc):
    assert svc.close_position() is None
    svc.place_order(REQ)
    svc.close_position()
    p = svc.client.calls[-1][1]
    assert p["side"] == "Sell" and p["reduceOnly"] is True and p["qty"] == "0.012"
    assert svc.position() is None


def test_kill_switch_halts_cancels_closes_and_blocks(svc):
    svc.place_order(REQ)
    svc.place_order(OrderRequest("Buy", 0.01, "Limit", 2, stop_loss=78_000, price=79_000))
    out = svc.kill_switch("테스트")
    assert out["errors"] == [] and svc.halted()
    assert svc.client.orders == [] and svc.position() is None
    with pytest.raises(RiskRejected, match="정지"):
        svc.place_order(REQ)
    svc.resume()
    assert not svc.halted()
    assert {"kill_switch", "resumed", "position_closed", "orders_cancelled"} <= set(_events(svc))


def test_kill_switch_continues_after_error(svc):
    svc.place_order(REQ)
    svc.client.fail["cancel_all"] = BybitError(10016, "server error", "/v5/order/cancel-all")
    out = svc.kill_switch("x")
    assert out["errors"] and out["close"] is not None and svc.position() is None and svc.halted()


def test_exchange_error_is_audited(svc):
    svc.client.fail["create_order"] = BybitError(110007, "insufficient balance", "/v5/order/create")
    with pytest.raises(BybitError):
        svc.place_order(REQ)
    assert _events(svc)[-1] == "order_error"


def test_key_check(svc):
    k = svc.key_check()
    assert k["withdraw"] is False and k["ip_restricted"] is False
    svc.client.api_key_info = lambda: {"readOnly": 0, "permissions": {"Wallet": ["Withdraw"]}, "ips": ["1.2.3.4"]}
    k = svc.key_check()
    assert k["withdraw"] is True and k["ip_restricted"] is True


def test_high_price_formatting(tmp_path):
    c = FakeClient(price=123_456.7)
    s = TradingService(c, "demo", tmp_path / "a", tmp_path / "r.yaml", live_unlocked=lambda: False)
    s.save_limits(RiskLimits(max_leverage=3, max_position_usdt=5_000, max_loss_per_trade_usdt=50,
                             max_daily_loss_usdt=100))
    s.place_order(OrderRequest("Buy", 0.01, "Limit", 2, stop_loss=122_456.7, price=123_000.3))
    p = c.calls[-1][1]
    assert p["price"] == "123000.3" and p["stopLoss"] == "122456.7"


def test_live_requires_confirm_flag(tmp_path):
    s = TradingService(FakeClient(), "live", tmp_path / "a", tmp_path / "r.yaml", live_unlocked=lambda: True)
    s.save_limits(LIM)
    with pytest.raises(RiskRejected, match="LIVE 주문 확인"):
        s.place_order(REQ)
    s.place_order(REQ, confirm_live=True)
    assert s.position() is not None


def test_unknown_result_audited(svc):
    from src.agent.service import OrderUnknown
    svc.client.fail["create_order"] = TimeoutError("read timeout")
    with pytest.raises(OrderUnknown, match="미체결"):
        svc.place_order(REQ)
    assert _events(svc)[-1] == "order_unknown"


def test_kill_switch_survives_halt_write_failure(svc, monkeypatch):
    svc.place_order(REQ)
    monkeypatch.setattr(risk, "set_halt", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    out = svc.kill_switch("x")
    assert any("halt" in e for e in out["errors"]) and svc.position() is None


def test_kill_switch_reports_residual_position(svc, monkeypatch):
    svc.place_order(REQ)
    monkeypatch.setattr(svc, "close_position", lambda reason="": None)
    out = svc.kill_switch("x")
    assert any("남아 있다" in e for e in out["errors"])
