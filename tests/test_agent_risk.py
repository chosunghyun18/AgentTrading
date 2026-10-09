"""agent.risk — 한도 검증·저장, 주문 사전 검사 9항목, 반올림, 정지 플래그, 감사 로그 가림."""

import json
from dataclasses import replace

import pytest

from src.agent import risk
from src.agent.risk import Context, OrderRequest, RiskLimits, Rules

RULES = Rules(qty_step=0.001, min_qty=0.001, max_mkt_qty=150, min_notional=5, tick=0.1)
LIM = RiskLimits(max_leverage=3, max_position_usdt=5_000, max_loss_per_trade_usdt=50, max_daily_loss_usdt=100)
CTX = Context(mode="demo", live_unlocked=False, halted=False, limits=LIM, ref_price=80_000, position_qty=0.0,
              today_pnl=0.0, rules=RULES)
OK = OrderRequest(side="Buy", qty=0.01, order_type="Market", leverage=2, stop_loss=79_600, take_profit=81_000)


def test_ok_order_passes_and_preview():
    assert risk.check_order(OK, CTX) == []
    p = risk.preview(OK, CTX)
    assert p["notional"] == pytest.approx(800) and p["margin"] == pytest.approx(400)
    assert p["loss_at_stop"] == pytest.approx(4) and p["gain_at_tp"] == pytest.approx(10) and p["rr"] == pytest.approx(2.5)


@pytest.mark.parametrize("req, ctx, needle", [
    (OK, replace(CTX, halted=True), "정지"),
    (OK, replace(CTX, limits=None), "한도 미설정"),
    (OK, replace(CTX, mode="live"), "LIVE 잠금"),
    (replace(OK, stop_loss=None), CTX, "손절가 필수"),
    (replace(OK, stop_loss=80_100), CTX, "매수 손절가"),
    (replace(OK, side="Sell", stop_loss=79_000, take_profit=None), CTX, "매도 손절가"),
    (replace(OK, take_profit=79_000), CTX, "익절가"),
    (replace(OK, leverage=5), CTX, "한도 3배"),
    (replace(OK, leverage=11), CTX, "10배 이하"),
    (replace(OK, qty=0.1), CTX, "주문 후 포지션"),           # 8,000 USDT > 5,000
    (replace(OK, qty=0.05, stop_loss=78_000), CTX, "손절 시 손실"),  # 0.05 × 2,000 = 100 > 50
    (OK, replace(CTX, today_pnl=-100), "일 손실 한도"),
    (replace(OK, qty=0.0001), CTX, "최소 0.001"),
    (replace(OK, qty=200), replace(CTX, limits=None), "시장가 최대"),
    (replace(OK, order_type="Limit", price=None), CTX, "가격이 필요"),
    (OK, replace(CTX, ref_price=0), "기준가"),
])
def test_violations(req, ctx, needle):
    v = risk.check_order(req, ctx)
    assert any(needle in x for x in v), v


def test_position_after_counts_existing_and_reduction():
    long_ = replace(CTX, position_qty=0.05)  # 기존 롱 4,000 USDT
    assert any("주문 후 포지션" in x for x in risk.check_order(replace(OK, qty=0.02), long_))  # 5,600
    sell = replace(OK, side="Sell", qty=0.02, stop_loss=80_400, take_profit=None)
    assert any("반대 방향" in x for x in risk.check_order(sell, long_))  # 줄이기·뒤집기는 청산 버튼으로


def test_limits_validation_and_roundtrip(tmp_path):
    with pytest.raises(ValueError):
        RiskLimits(max_leverage=20, max_position_usdt=1, max_loss_per_trade_usdt=1, max_daily_loss_usdt=1)
    with pytest.raises(ValueError):
        RiskLimits(max_leverage=2, max_position_usdt=0, max_loss_per_trade_usdt=1, max_daily_loss_usdt=1)
    p = tmp_path / "risk.yaml"
    assert risk.load_limits(p) is None
    risk.save_limits(p, LIM)
    assert risk.load_limits(p) == LIM
    p.write_text("max_leverage: 50\n", encoding="utf-8")
    assert risk.load_limits(p) is None


def test_rounding():
    assert risk.round_qty(0.0129, 0.001) == 0.012
    assert risk.round_price(80000.06, 0.1) == 80000.1
    assert risk.round_price(79999.94, 0.1) == 79999.9


def test_rules_from_instrument():
    from tests.agent_fakes import INSTRUMENT
    assert risk.Rules.from_instrument(INSTRUMENT) == Rules(0.001, 0.001, 150, 5, 0.1)


def test_halt_and_audit(tmp_path):
    assert not risk.is_halted(tmp_path)
    risk.set_halt(tmp_path, "테스트")
    assert risk.is_halted(tmp_path) and "테스트" in risk.halt_reason(tmp_path)
    risk.clear_halt(tmp_path)
    assert not risk.is_halted(tmp_path)
    risk.audit(tmp_path, "x", params={"api_key": "K", "sign": "S", "qty": "1"})
    risk.audit(tmp_path, "y")
    rows = risk.read_audit(tmp_path)
    assert [r["event"] for r in rows] == ["y", "x"]  # 최신 먼저
    raw = risk.audit_path(tmp_path).read_text(encoding="utf-8")
    assert '"K"' not in raw and '"S"' not in raw and json.loads(raw.splitlines()[0])["params"]["qty"] == "1"


@pytest.mark.parametrize("x, want", [(123456.7, "123456.7"), (100000.0, "100000"), (0.001, "0.001"),
                                     (0.00001, "0.00001"), (82713.1, "82713.1"), (3.0, "3")])
def test_num_str_no_precision_loss(x, want):
    assert risk.num_str(x) == want


def test_loss_counts_whole_position_with_existing_avg():
    ctx = replace(CTX, position_qty=0.02, position_avg=81_000)  # 기존 롱 0.02 @81,000
    req = replace(OK, qty=0.01, stop_loss=79_600)
    # 기존 0.02 × 1,400 + 신규 0.01 × 400 = 32
    assert risk.preview(req, ctx)["loss_at_stop"] == pytest.approx(32)
    assert any("포지션 전체" in x for x in risk.check_order(replace(req, qty=0.03), replace(ctx, position_qty=0.03)))


def test_open_orders_count_toward_exposure():
    assert any("미체결 포함" in x for x in risk.check_order(OK, replace(CTX, open_order_qty=0.06)))


def test_stop_beyond_liquidation_rejected():
    req = replace(OK, leverage=3, stop_loss=80_000 * (1 - 0.30), qty=0.001)  # 30% > 0.8/3 ≈ 26.7%
    assert any("청산 거리" in x for x in risk.check_order(req, replace(CTX, limits=None)))


def test_margin_vs_available():
    assert any("증거금" in x for x in risk.check_order(OK, replace(CTX, available=100)))  # 필요 400


def test_projected_daily_loss():
    # 오늘 −98, 손절 시 −4 → −102 < −100
    assert any("손절되면" in x for x in risk.check_order(OK, replace(CTX, today_pnl=-98)))
    assert risk.check_order(OK, replace(CTX, today_pnl=-95)) == []


@pytest.mark.parametrize("field, val", [("stop_loss", float("nan")), ("qty", float("inf")), ("leverage", float("nan"))])
def test_non_finite_rejected(field, val):
    assert any("NaN" in x for x in risk.check_order(replace(OK, **{field: val}), CTX))
