"""W2 최소 — 런타임: shadow ≡ 백테스트 진입, paper 는 TradingService 경유, 정지 명령, 거부 조건, kline 정리."""

import pandas as pd
import pytest

from src.agent.bybit import BybitError
from src.agent.risk import RiskLimits
from src.agent.service import TradingService
from src.runtime import feed
from src.runtime.brokers import BybitBroker, NullBroker, RunAbort
from src.runtime.runtime import AgentRuntime
from src.store import db
from src.strategy import agent, registry
from src.strategy.engine import batch_run
from src.strategy.testkit import synthetic_bars
from tests.agent_fakes import FakeClient

LIM = RiskLimits(max_leverage=3, max_position_usdt=5_000, max_loss_per_trade_usdt=50, max_daily_loss_usdt=100)
PARAMS = {"fast": 3, "slow": 8, "max_hold": 20}


@pytest.fixture
def con(tmp_path):
    c = db.connect(tmp_path / "s.sqlite")
    yield c
    c.close()


def _agent(con, mode):
    cfg = agent.from_mapping({"id": f"{mode}-1", "strategy": "sma_cross", "mode": mode, "params": PARAMS})
    db.add_agent(con, cfg)
    return cfg


def _rows(bars):
    return [dict(r) for r in bars[["ts", "open", "high", "low", "close"]].to_dict("records")]


def test_shadow_entries_match_backtest(con):
    bars = synthetic_bars(1500, seed=5, price=80_000)
    cfg = _agent(con, "shadow")
    run = db.start_run(con, cfg.id, "shadow")
    rt = AgentRuntime(con, cfg, NullBroker(1000, cfg.risk_frac, cfg.max_leverage), run)
    for b in _rows(bars):
        rt.on_bar(b)
    want = batch_run(registry.get("sma_cross"), PARAMS, bars).signals
    got = [(pd.Timestamp(e["ts"]), e["side"]) for e in reversed(db.events(con, run, 10_000)) if e["kind"] == "signal"]
    assert len(want) > 10 and got == want
    kinds = {e["kind"] for e in db.events(con, run, 10_000)}
    assert {"signal", "pending", "entry", "exit"} <= kinds
    assert len(db.equity(con, run)) == len(bars)


def test_runtime_skips_duplicates_and_resets_on_gap(con):
    cfg = _agent(con, "shadow")
    run = db.start_run(con, cfg.id, "shadow")
    rt = AgentRuntime(con, cfg, NullBroker(), run)
    rows = _rows(synthetic_bars(20))
    rt.prime(rows[:10])
    assert len(rt.window) == 9  # warmup = slow + 1
    rt.on_bar(rows[5])  # 과거 봉 무시
    assert rt.last_ts == rows[9]["ts"]
    rt.on_bar(rows[12])  # 2분 공백
    assert len(rt.window) == 1 and db.events(con, run)[0]["kind"] == "gap"


def test_stop_command_finishes_run(con):
    cfg = _agent(con, "shadow")
    run = db.start_run(con, cfg.id, "shadow")
    rt = AgentRuntime(con, cfg, NullBroker(), run)
    rows = _rows(synthetic_bars(5))
    rt.on_bar(rows[0])
    db.enqueue_command(con, cfg.id, "stop")
    rt.on_bar(rows[1])
    rt.on_bar(rows[2])
    assert rt.status == "stopped" and db.list_runs(con, cfg.id)[0]["status"] == "stopped"
    assert rt.last_ts == rows[1]["ts"]


def test_runtime_refuses_mode_mismatch(con):
    cfg = _agent(con, "paper")
    with pytest.raises(ValueError):
        AgentRuntime(con, cfg, NullBroker(), 1)


def _svc(tmp_path, client=None, limits=True, enroll=True, mode="demo"):
    s = TradingService(client or FakeClient(), mode, tmp_path / "agent", tmp_path / "risk.yaml",
                       live_unlocked=lambda: False)
    if limits:
        s.save_limits(LIM)
    if enroll:
        s.initialize_drawdown(confirm=True)
    return s


@pytest.mark.parametrize("kw,match", [({"limits": False}, "한도"), ({"enroll": False}, "MDD"),
                                      ({"mode": "live"}, "demo"), ({"client": FakeClient(has_keys=False)}, "키")])
def test_paper_preconditions(tmp_path, kw, match):
    with pytest.raises(RunAbort, match=match):
        BybitBroker(_svc(tmp_path, **kw), "BTCUSDT", 0.01, 2)
    with pytest.raises(RunAbort, match="심볼"):
        BybitBroker(_svc(tmp_path / "x"), "ETHUSDT", 0.01, 2)


def _paper(con, tmp_path):
    client = FakeClient()
    svc = _svc(tmp_path, client)
    cfg = _agent(con, "paper")
    run = db.start_run(con, cfg.id, "paper")
    return client, svc, cfg, run, AgentRuntime(con, cfg, BybitBroker(svc, cfg.symbol, cfg.risk_frac, cfg.max_leverage), run)


def _feed_until_order(client, rt, con, run, bars):
    for b in _rows(bars):
        client.price = b["close"]
        rt.on_bar(b)
        if any(e["kind"] == "order" for e in db.events(con, run)):
            return b
    raise AssertionError("주문이 나가지 않았다")


def test_paper_orders_via_service_and_stop_closes(con, tmp_path):
    client, svc, cfg, run, rt = _paper(con, tmp_path)
    bars = synthetic_bars(400, seed=5, price=80_000)
    _feed_until_order(client, rt, con, run, bars)
    orders = [kw for name, kw in client.calls if name == "create_order"]
    assert len(orders) == 1 and orders[0]["orderType"] == "Market" and orders[0]["stopLoss"]
    assert svc.position() is not None
    db.enqueue_command(con, cfg.id, "stop")
    nxt = bars.iloc[[-1]].assign(ts=pd.Timestamp("2030-01-01", tz="UTC"))
    rt.on_bar(_rows(nxt)[0])
    closes = [kw for name, kw in client.calls if name == "create_order" and kw.get("reduceOnly")]
    assert len(closes) == 1 and rt.status == "stopped" and svc.position() is None


def test_paper_does_not_reenter_while_position_open(con, tmp_path):
    client, svc, cfg, run, rt = _paper(con, tmp_path)
    for b in _rows(synthetic_bars(400, seed=5, price=80_000)):
        client.price = b["close"]
        rt.on_bar(b)
    entries = [kw for name, kw in client.calls if name == "create_order" and not kw.get("reduceOnly")]
    assert 1 <= len(entries)
    # FakeClient 는 손절·익절을 집행하지 않으므로 포지션이 계속 열려 있다 → max_hold 청산 뒤에만 재진입
    closes = [kw for name, kw in client.calls if name == "create_order" and kw.get("reduceOnly")]
    assert len(entries) <= len(closes) + 1
    assert any(e["kind"] == "close" and e["reason"] == "time" for e in db.events(con, run, 10_000))


def test_paper_unknown_order_aborts(con, tmp_path):
    client, svc, cfg, run, rt = _paper(con, tmp_path)

    def boom(**kw):
        raise TimeoutError("x")
    client.create_order = boom
    bars = synthetic_bars(400, seed=5, price=80_000)
    for b in _rows(bars):
        client.price = b["close"]
        rt.on_bar(b)
        if rt.status:
            break
    assert rt.status == "uncertain"
    assert db.list_runs(con, cfg.id)[0]["status"] == "uncertain"


def test_paper_rejection_is_event_not_crash(con, tmp_path):
    client, svc, cfg, run, rt = _paper(con, tmp_path)
    client.fail["create_order"] = BybitError(10001, "bad", "/v5/order/create")
    for b in _rows(synthetic_bars(400, seed=5, price=80_000)):
        client.price = b["close"]
        rt.on_bar(b)
    assert rt.status is None
    assert any(e["kind"] == "error" for e in db.events(con, run, 10_000))


def test_closed_bars_orders_and_drops_forming():
    t0 = 1_790_000_000_000
    rows = [[str(t0 + i * 60_000), "1", "2", "0.5", str(i), "1", "1"] for i in (3, 2, 1, 0, 1)]  # 최신 → 과거, 중복
    out = feed.closed_bars(rows, now_ms=t0 + 3 * 60_000 + 10)
    assert [b["close"] for b in out] == [0, 1, 2]
    assert out[0]["ts"] == pd.Timestamp(t0, unit="ms", tz="UTC")


def test_run_loop_with_fake_clock(con):
    cfg = _agent(con, "shadow")
    run = db.start_run(con, cfg.id, "shadow")
    rt = AgentRuntime(con, cfg, NullBroker(), run)
    clock = {"now": 1_790_000_000_000 + 30_000}
    client = FakeClient()

    def klines(symbol, interval="1", limit=200):
        last = clock["now"] // 60_000 * 60_000
        return [[str(last - i * 60_000), "80000", "80050", "79950", "80000", "1", "1"] for i in range(limit)]
    client.klines = klines
    n = feed.run_loop(rt, client, "BTCUSDT", max_bars=3, sleep=lambda s: clock.update(now=clock["now"] + int(s * 1000)),
                      now_ms=lambda: clock["now"])
    assert n == 3 and len(db.equity(con, run)) == 3


def test_paper_failed_max_hold_close_retried(con, tmp_path):
    client, svc, cfg, run, rt = _paper(con, tmp_path)
    bars = _rows(synthetic_bars(400, seed=5, price=80_000))
    i = 0
    while not any(e["kind"] == "order" for e in db.events(con, run)):
        client.price = bars[i]["close"]
        rt.on_bar(bars[i])
        i += 1
    client.fail["create_order"] = BybitError(10006, "rate", "/v5/order/create")
    for b in bars[i:i + 25]:
        client.price = b["close"]
        rt.on_bar(b)
    fails = [e for e in db.events(con, run, 10_000) if e["kind"] == "close"]
    assert len(fails) >= 2 and all("error" in e["result"] for e in fails)  # 매 봉 다시 시도
    del client.fail["create_order"]
    client.price = bars[i + 25]["close"]
    rt.on_bar(bars[i + 25])
    assert svc.position() is None and rt.status is None


def test_paper_transient_read_error_skips_bar_then_aborts(con, tmp_path):
    client, svc, cfg, run, rt = _paper(con, tmp_path)
    bars = _rows(synthetic_bars(20, seed=5, price=80_000))
    orig = client.positions
    client.positions = lambda symbol: (_ for _ in ()).throw(TimeoutError("x"))
    rt.on_bar(bars[0])
    assert rt.status is None and db.events(con, run)[0]["kind"] == "skip_bar"
    client.positions = orig
    rt.on_bar(bars[1])
    client.positions = lambda symbol: (_ for _ in ()).throw(TimeoutError("x"))
    for b in bars[2:10]:
        rt.on_bar(b)
    assert rt.status == "error"


def test_paper_stop_close_failure_is_uncertain(con, tmp_path):
    client, svc, cfg, run, rt = _paper(con, tmp_path)
    bars = _rows(synthetic_bars(400, seed=5, price=80_000))
    _feed_until_order(client, rt, con, run, synthetic_bars(400, seed=5, price=80_000))
    client.fail["create_order"] = BybitError(10006, "rate", "/v5/order/create")
    db.enqueue_command(con, cfg.id, "stop")
    rt.on_bar({**bars[-1], "ts": pd.Timestamp("2030-01-01", tz="UTC")})
    assert rt.status == "uncertain" and svc.position() is not None


def test_paper_adopts_existing_position(con, tmp_path):
    client, svc, cfg, run, rt = _paper(con, tmp_path)
    client.pos.update(side="Buy", size="0.01", avgPrice="80000")
    rt.on_bar(_rows(synthetic_bars(1, price=80_000))[0])
    kinds = [e["kind"] for e in db.events(con, run)]
    assert "adopted" in kinds and not any(n == "create_order" for n, _ in client.calls)
