"""콘솔 화면 AppTest — 모의 거래소(FakeClient)로 주문·거부·청산·킬스위치·한도·LIVE 잠금. 실제 Bybit 호출 없음."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.agent import risk, service, settings
from src.agent.risk import RiskLimits
from src.agent.service import TradingService
from tests.agent_fakes import FakeClient

VIEWS = Path(__file__).resolve().parents[1] / "src" / "ui" / "views"
LIM = RiskLimits(max_leverage=3, max_position_usdt=5_000, max_loss_per_trade_usdt=50, max_daily_loss_usdt=100)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """모드별 FakeClient 하나씩, 서비스 팩토리 주입, 키·LIVE 환경 초기화."""
    for n in ("BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET", "BYBIT_LIVE_API_KEY", "BYBIT_LIVE_API_SECRET",
              settings.LIVE_FLAG):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("AT_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setattr(settings, "load_env", lambda: None)
    clients = {"demo": FakeClient(), "live": FakeClient()}
    w = {"clients": clients, "dir": tmp_path / "agent", "risk": tmp_path / "risk.yaml"}
    monkeypatch.setattr(service, "FACTORY", lambda mode: TradingService(clients[mode], mode, w["dir"], w["risk"]))
    return w


def _run(page: str) -> AppTest:
    at = AppTest.from_file(str(VIEWS / page), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _text(els) -> str:
    return "\n".join(str(e.value) for e in els)


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def _fill_order(at, side="Buy", qty=0.01, sl=79_600.0, tp=81_000.0, lev=2):
    at.radio(key="o-side").set_value(side)
    at.number_input(key="o-qty").set_value(qty)
    at.number_input(key="o-lev").set_value(lev)
    at.number_input(key="o-sl").set_value(sl)
    at.number_input(key="o-tp").set_value(tp)
    _btn(at, "미리보기 · 리스크 검사").click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_trade_without_limits_is_blocked(world):
    at = _fill_order(_run("console_trade.py"))
    assert "한도 미설정" in _text(at.error)
    assert all(c[0] != "create_order" for c in world["clients"]["demo"].calls)


def test_trade_preview_then_place(world):
    risk.save_limits(world["risk"], LIM)
    at = _fill_order(_run("console_trade.py"))
    assert "검사 통과" in _text(at.success)
    at.button(key="order-btn").click().run()
    assert not at.exception
    assert "주문 접수" in _text(at.success)
    calls = [c for c in world["clients"]["demo"].calls if c[0] == "create_order"]
    assert len(calls) == 1 and calls[0][1]["stopLoss"] == "79600"
    assert "DEMO" in _text(at.markdown)


def test_trade_rejects_bad_stop(world):
    risk.save_limits(world["risk"], LIM)
    at = _fill_order(_run("console_trade.py"), sl=80_500.0)
    assert "매수 손절가" in _text(at.error)
    assert not any(getattr(b, "key", None) == "order-btn" for b in at.button)


def test_close_position(world):
    c = world["clients"]["demo"]
    c.pos.update(side="Buy", size="0.01", avgPrice="80000", unrealisedPnl="1")
    at = _run("console_trade.py")
    at.checkbox(key="close-confirm").check().run()
    at.button(key="close-btn").click().run()
    assert not at.exception and "청산 주문" in _text(at.success)
    assert c.pos["size"] == "0"


def test_no_keys_shows_guide(world):
    world["clients"]["demo"].has_keys = False
    at = _run("console_account.py")
    assert "API 키가 없다" in _text(at.info)
    assert any("Demo API 키" in m.value for m in at.markdown)


def test_account_with_keys(world):
    at = _run("console_account.py")
    assert "인증 연결 정상" in _text(at.success) and "출금 권한 없음" in _text(at.success)
    assert "IP 제한 없음" in _text(at.warning)
    assert not any(getattr(b, "key", None) == "to-live" for b in at.button)  # 잠김


def test_live_switch_requires_unlock_and_ack(world, monkeypatch):
    monkeypatch.setenv("BYBIT_LIVE_API_KEY", "k")
    monkeypatch.setenv("BYBIT_LIVE_API_SECRET", "s")
    monkeypatch.setenv(settings.LIVE_FLAG, "1")
    at = _run("console_account.py")
    assert at.button(key="to-live").disabled
    at.checkbox(key="live-ack").check().run()
    at.button(key="to-live").click().run()
    assert settings.get_mode() == "live"
    at = _run("console_trade.py")
    assert "LIVE" in _text(at.markdown)
    risk.save_limits(world["risk"], LIM)
    at = _fill_order(at)
    assert at.button(key="order-btn").disabled  # LIVE 는 확인 체크 필요
    at.checkbox(key="live-confirm").check().run()
    at.button(key="order-btn").click().run()
    assert [c for c in world["clients"]["live"].calls if c[0] == "create_order"]
    assert not [c for c in world["clients"]["demo"].calls if c[0] == "create_order"]


def test_risk_limits_and_kill_switch(world):
    at = _run("console_risk.py")
    assert "한도가 없어" in _text(at.warning)
    at.number_input(key="l-lev").set_value(3.0)
    at.number_input(key="l-pos").set_value(5000.0)
    at.number_input(key="l-per").set_value(50.0)
    at.number_input(key="l-day").set_value(100.0)
    _btn(at, "저장").click().run()
    assert risk.load_limits(world["risk"]) == LIM
    c = world["clients"]["demo"]
    c.pos.update(side="Sell", size="0.02")
    at.checkbox(key="kill-ok").check().run()
    at.button(key="kill-btn").click().run()
    assert not at.exception and "완료" in _text(at.success)
    assert risk.is_halted(world["dir"]) and c.pos["size"] == "0"
    at = _run("console_risk.py")
    at.checkbox(key="resume-ok").check().run()
    at.button(key="resume-btn").click().run()
    assert not risk.is_halted(world["dir"])


def test_risk_rejects_invalid_limits(world):
    at = _run("console_risk.py")
    at.number_input(key="l-lev").set_value(3.0)
    _btn(at, "저장").click().run()  # 나머지 비움 → 0 → 거부
    assert "저장 안 됨" in _text(at.error) and risk.load_limits(world["risk"]) is None


def test_history_and_audit(world):
    risk.audit(world["dir"], "order_placed", mode="demo", params={"qty": "0.01"})
    at = _run("console_history.py")
    assert len(at.dataframe) >= 2  # 체결 + 감사 로그


def test_exchange_error_shown_not_crash(world):
    from src.agent.bybit import BybitError
    c = world["clients"]["demo"]
    c.ticker = lambda s: (_ for _ in ()).throw(BybitError(10002, "timeout", "/v5/market/tickers"))
    at = _run("console_trade.py")
    assert "시세 조회 실패" in _text(at.error)


def test_confirm_checkbox_resets_after_action(world):
    risk.save_limits(world["risk"], LIM)
    c = world["clients"]["demo"]
    c.pos.update(side="Buy", size="0.01")
    at = _run("console_risk.py")
    at.checkbox(key="kill-ok").check().run()
    at.button(key="kill-btn").click().run()
    assert at.checkbox(key="kill-ok").value is False and at.button(key="kill-btn").disabled
    assert "정지(HALT)" in _text(at.markdown)  # 배너가 같은 실행에서 갱신


def test_live_switch_blocked_for_withdraw_key(world, monkeypatch):
    monkeypatch.setenv("BYBIT_LIVE_API_KEY", "k")
    monkeypatch.setenv("BYBIT_LIVE_API_SECRET", "s")
    monkeypatch.setenv(settings.LIVE_FLAG, "1")
    world["clients"]["live"].api_key_info = lambda: {"readOnly": 0, "permissions": {"Wallet": ["Withdraw"]},
                                                     "ips": ["1.2.3.4"]}
    at = _run("console_account.py")
    at.checkbox(key="live-ack").check().run()
    at.button(key="to-live").click().run()
    assert "출금 권한" in _text(at.error) and settings.get_mode() == "demo"
