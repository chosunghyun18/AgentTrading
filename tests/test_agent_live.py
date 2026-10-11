"""agent.live·agent.latency — 모의 스트림·모의 거래소로 봉 경계·늦은 체결·캐시·대조 차단·라우터·지연 집계 검증.

실제 Bybit 호출·주문 없음.
"""

import asyncio
import hashlib
import hmac
import json

import pytest

from src.agent import risk
from src.agent.bybit import BybitError
from src.agent.latency import LatencyRecorder, Trace, read_records, summarize
from src.agent.live import check_bars
from src.agent.live.bars import (Bar, BarBuilder, Trade, compare_klines, fill_from_klines, parse_public_trades,
                                 to_kline_convention)
from src.agent.live.clock import ExchangeClock
from src.agent.live.router import DuplicateSignal, Entry, OrderRouter, build_entry, link_id
from src.agent.live.state_cache import Snapshot, StateCache
from src.agent.live.ws import AuthError, auth_message, run_stream
from src.agent.risk import RiskLimits, Rules
from src.agent.service import OrderUnknown, RiskRejected
from tests.agent_fakes import INSTRUMENT, FakeClient

M = 60_000
T0 = 1_790_000_040 * 1000 - (1_790_000_040 * 1000) % M  # 분 경계
RULES = Rules.from_instrument(INSTRUMENT)


# 봉 -----------------------------------------------------------------------------------------------

def _trades(b, *rows):
    for i, (ts, p, v) in enumerate(rows):
        b.on_trade(Trade(ts, p, v, f"id{ts}-{i}"))


def test_bar_boundary_and_close_delay():
    b = BarBuilder()
    _trades(b, (T0 + 10, 100, 1), (T0 + M - 1, 101, 2), (T0 + M, 105, 1))
    assert b.poll(T0 + M + 49) == []                       # 마감 + 50 ms 전에는 닫지 않는다
    bars = b.poll(T0 + M + 50)
    assert len(bars) == 1
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume) == (100, 101, 100, 101, 3)
    assert bars[0].partial                                 # 첫 봉은 분 중간부터 받았을 수 있다
    nxt = b.poll(T0 + 2 * M + 50)[0]
    assert nxt.start_ms == T0 + M and nxt.open == 105 and nxt.usable


def test_late_trade_counted_not_applied():
    b = BarBuilder()
    _trades(b, (T0 + 1, 100, 1))
    b.poll(T0 + M + 50)
    _trades(b, (T0 + 30_000, 999, 5))                      # 닫힌 봉 구간에 늦게 온 체결
    assert b.late[T0] == 1
    _trades(b, (T0 + M + 1, 101, 1))
    bar = b.poll(T0 + 2 * M + 50)[0]
    assert bar.high == 101 and bar.volume == 1


def test_empty_minute_is_flat_bar_and_duplicates_dropped():
    b = BarBuilder()
    b.on_trade(Trade(T0 + 1, 100, 1, "x"))
    b.on_trade(Trade(T0 + 1, 100, 1, "x"))                 # 재연결 중복
    assert b.duplicates == 1
    bars = b.poll(T0 + 3 * M + 50)
    assert [x.start_ms for x in bars] == [T0, T0 + M, T0 + 2 * M]
    assert bars[0].volume == 1
    assert (bars[1].open, bars[1].close, bars[1].volume, bars[1].trades) == (100, 100, 0, 0)


def test_disconnect_gap_marks_suspect_and_rest_fill():
    b = BarBuilder()
    _trades(b, (T0 + 1, 100, 1))
    b.on_disconnect(T0 + M + 5_000)
    b.on_reconnect(T0 + 2 * M + 10_000)
    _trades(b, (T0 + 2 * M + 20_000, 103, 1))
    bars = b.poll(T0 + 4 * M + 50)
    assert [x.suspect for x in bars] == [False, True, True, False]
    kl = [[str(T0 + M), "100", "102", "99", "101", "7", "0"]]
    filled = fill_from_klines(bars, kl)
    assert filled[1].source == "rest" and filled[1].close == 101 and filled[1].usable
    assert filled[2].suspect                                # REST 에도 없으면 의심 그대로


def test_parse_and_compare_klines_separates_volume():
    msg = {"topic": "publicTrade.BTCUSDT", "data": [{"T": T0 + 5, "p": "100.5", "v": "0.010", "S": "Buy", "i": "a"}]}
    assert parse_public_trades(msg) == [Trade(T0 + 5, 100.5, 0.01, "a")]
    bars = [Bar(T0, 100, 101, 99, 100, 1, 3, partial=True), Bar(T0 + M, 100.5, 101, 99.5, 100.0, 2, 3),
            Bar(T0 + 2 * M, 100, 101, 99, 100, 2, 3)]
    # kline 규약: open = 직전 종가(100), high·low 에 직전 종가 포함
    kl = [[str(T0 + M), "100", "101", "99.5", "100.05", "2", "0"],      # 호가 절반 이내 → 일치
          [str(T0 + 2 * M), "100", "101", "99", "100", "2.5", "0"]]     # 거래량만 다름
    r = compare_klines(bars, kl, tick=0.1)
    assert r["compared"] == 2 and r["price_mismatch"] == 0 and r["volume_mismatch"] == 1
    assert r["rate"] == 0 and r["volume_rate"] == 0.5
    kl[0][4] = "100.2"
    assert compare_klines(bars, kl, tick=0.1)["price_mismatch"] == 1


def test_kline_convention_open_is_prev_close():
    b = Bar(T0, 101, 102, 100.5, 101.5, 1, 2)
    assert to_kline_convention(b, 100) == (100, 102, 100, 101.5)
    assert to_kline_convention(b, None) == (None, 102, 100.5, 101.5)


def test_exchange_clock_offset_uses_midpoint():
    t = iter([0.0, 0.0, 0.1, 0.1])
    c = ExchangeClock(wall=lambda: 1000.0, mono=lambda: next(t))
    rtt = c.sync(lambda: 1_000_500)                          # 왕복 100 ms, 중간 시점 로컬 1 000 050 ms
    assert rtt == pytest.approx(100) and c.offset_ms == pytest.approx(450)


def test_exchange_clock_sync_best_keeps_min_rtt_sample():
    # 표본 1: 첫 연결(왕복 3 000 ms, 오프셋 오차 큼) → 표본 2: 왕복 40 ms → 표본 3: 왕복 200 ms
    t = iter([0.0, 0.0, 3.0, 3.0, 3.04, 3.04, 3.24])   # 첫 값은 생성 시 기준점
    c = ExchangeClock(wall=lambda: 1000.0, mono=lambda: next(t))
    server = iter([1_000_000, 1_003_520, 1_003_640])
    rtt = c.sync_best(lambda: next(server), samples=3)
    assert rtt == pytest.approx(40) and c.rtt_ms == pytest.approx(40)
    assert c.offset_ms == pytest.approx(1_003_520 - 1_003_020)   # 중간 시점 로컬 1 003 020 ms


# 상태 캐시 ------------------------------------------------------------------------------------------

POS = {"symbol": "BTCUSDT", "side": "Buy", "size": "0.01", "avgPrice": "80000", "unrealisedPnl": "-3",
       "cumRealisedPnl": "-50"}


def _snap(pos=POS, orders=(), closed=-20.0, now=T0):
    return Snapshot(dict(pos) if pos else None, list(orders), {"totalAvailableBalance": "900"}, closed, now)


def _cache():
    c = StateCache("BTCUSDT")
    c.set_connected(True)
    c.load_snapshot(_snap())
    return c


def test_today_pnl_from_cum_realised_baseline_and_day_roll():
    c = _cache()
    assert c.baseline == -30                                  # −50 − (−20)
    assert c.today_pnl == pytest.approx(-20 - 3)
    c.apply({"topic": "position", "data": [{"symbol": "BTCUSDT", "side": "", "size": "0", "cumRealisedPnl": "-65",
                                           "unrealisedPnl": "0"}]})
    assert c.position_qty == 0 and c.today_pnl == pytest.approx(-35)
    assert c.roll_day(T0 + 86_400_000) and c.today_pnl == 0


def test_order_and_wallet_updates_and_listeners():
    c = _cache()
    seen = []
    c.listeners.append(lambda topic, item: seen.append((topic, item.get("orderStatus"))))
    c.apply({"topic": "order", "data": [{"symbol": "BTCUSDT", "orderId": "o1", "orderStatus": "New", "qty": "0.02",
                                        "cumExecQty": "0.005", "reduceOnly": False},
                                       {"symbol": "BTCUSDT", "orderId": "o2", "orderStatus": "New", "qty": "0.01",
                                        "reduceOnly": True}]})
    assert c.open_order_qty == pytest.approx(0.015)
    c.apply({"topic": "order", "data": [{"symbol": "BTCUSDT", "orderId": "o1", "orderStatus": "Cancelled",
                                        "rejectReason": "EC_PostOnlyWillTakeLiquidity"}]})
    assert set(c.orders) == {"o2"} and ("order", "Cancelled") in seen
    c.apply({"topic": "wallet", "data": [{"totalAvailableBalance": "750"}]})
    assert c.available == 750


def test_blockers_until_snapshot_and_on_disconnect():
    c = StateCache("BTCUSDT")
    assert any("미초기화" in v for v in c.blockers()) and any("연결 없음" in v for v in c.blockers())
    c = _cache()
    assert c.blockers() == []
    c.set_connected(False)
    c.set_connected(True)
    assert any("미초기화" in v for v in c.blockers())        # 재연결 뒤에도 스냅샷 전까지 막힌다
    c.load_snapshot(_snap())
    assert c.blockers() == []


def test_reconcile_blocks_after_two_mismatches_and_snapshot_clears(tmp_path):
    events = []
    c = StateCache("BTCUSDT", audit=lambda ev, **k: events.append(ev))
    c.set_connected(True)
    c.load_snapshot(_snap())
    other = dict(POS, size="0.02")
    assert c.reconcile(_snap(pos=other)) and not c.blocked     # 1회는 봐준다
    assert c.reconcile(_snap()) == [] and c.mismatches == 0    # 일치하면 계수 초기화
    c.reconcile(_snap(pos=other))
    c.reconcile(_snap(orders=[{"orderId": "zz", "orderStatus": "New"}], pos=other))
    assert c.blocked and any("REST 대조" in v for v in c.blockers())
    assert "live_cache_blocked" in events
    c.load_snapshot(_snap(pos=other))
    assert c.blockers() == [] and "live_cache_unblocked" in events


def test_reconcile_detects_cum_realised_drift():
    c = _cache()
    drift = dict(POS, cumRealisedPnl="-51")
    assert any("cumRealisedPnl" in d for d in c.reconcile(_snap(pos=drift)))


# 라우터 ------------------------------------------------------------------------------------------------

LIM = RiskLimits(max_leverage=4, max_position_usdt=5_000, max_loss_per_trade_usdt=50, max_daily_loss_usdt=100)


class Client(FakeClient):
    def order_by_link(self, symbol, link):
        self._call("order_by_link", link=link)
        return [o for o in self.orders if o.get("orderLinkId") == link]


@pytest.fixture
def router(tmp_path):
    risk.save_limits(tmp_path / "risk.yaml", LIM)
    c = StateCache("BTCUSDT")
    c.set_connected(True)
    c.load_snapshot(_snap(pos=None, closed=0.0))
    r = OrderRouter(Client(), c, mode="demo", state_dir=tmp_path / "agent", limits_path=tmp_path / "risk.yaml",
                    symbol="BTCUSDT", live_unlocked=lambda: False)
    r.start()
    return r


def _created(r):
    return [kw for name, kw in r.client.calls if name == "create_order"]


def test_build_entry_request_matches_params():
    req, p = build_entry(Entry("Buy", 0.0129, "postonly", 79_600.04, price=79_999.96), RULES, "BTCUSDT", "L")
    assert (req.order_type, req.qty, req.price, req.stop_loss, req.leverage) == ("Limit", 0.012, 80_000, 79_600, 4)
    assert p["qty"] == "0.012" and p["price"] == "80000" and p["stopLoss"] == "79600" and p["timeInForce"] == "PostOnly"
    req, p = build_entry(Entry("Sell", 0.01, "ioc", 80_400, price=80_000), RULES, "BTCUSDT", "L")
    assert p["timeInForce"] == "IOC" and req.order_type == "Limit"
    req, p = build_entry(Entry("Buy", 0.01, "market", 79_600), RULES, "BTCUSDT", "L")
    assert req.order_type == "Market" and "timeInForce" not in p and "price" not in p
    with pytest.raises(ValueError):
        build_entry(Entry("Buy", 0.01, "fok", 79_600), RULES, "BTCUSDT", "L")


def test_leverage_set_once_at_start_not_per_order(router):
    router.submit(Entry("Buy", 0.01, "postonly", 79_600, price=80_000), "sig-1", last_price=80_000)
    router.submit(Entry("Buy", 0.01, "market", 79_600), "sig-2", last_price=80_000)
    names = [n for n, _ in router.client.calls if n in ("set_leverage", "create_order")]
    assert names == ["set_leverage", "create_order", "create_order"]
    assert router.client.calls[0] == ("set_leverage", {"leverage": 4.0})


def test_link_id_deterministic_persisted_and_not_resent(router, tmp_path):
    e = Entry("Buy", 0.01, "postonly", 79_600, price=80_000)
    router.submit(e, "sig-1", last_price=80_000)
    assert _created(router)[0]["orderLinkId"] == link_id("sig-1") and len(link_id("sig-1")) == 36
    with pytest.raises(DuplicateSignal):
        router.submit(e, "sig-1", last_price=80_000)
    again = OrderRouter(Client(), router.cache, mode="demo", state_dir=router.state_dir,
                        limits_path=router.limits_path, symbol="BTCUSDT", live_unlocked=lambda: False)
    again.start()
    with pytest.raises(DuplicateSignal):                      # 재시작해도 같은 신호는 다시 안 보낸다
        again.submit(e, "sig-1", last_price=80_000)


def test_blocked_cache_or_risk_violation_never_reaches_exchange(router):
    router.cache.set_connected(False)
    with pytest.raises(RiskRejected, match="개인 WS"):
        router.submit(Entry("Buy", 0.01, "market", 79_600), "sig-1", last_price=80_000)
    router.cache.set_connected(True)
    router.cache.load_snapshot(_snap(pos=None, closed=0.0))
    with pytest.raises(RiskRejected, match="1회 한도"):
        router.submit(Entry("Buy", 0.1, "market", 79_000), "sig-2", last_price=80_000)
    assert _created(router) == []
    assert risk.read_audit(router.state_dir)[0]["event"] == "live_order_rejected"


def test_unknown_result_not_retried_and_resolved(router):
    router.client.fail["create_order"] = TimeoutError("read timeout")
    with pytest.raises(OrderUnknown):
        router.submit(Entry("Buy", 0.01, "market", 79_600), "sig-1", last_price=80_000)
    assert len(_created(router)) == 1
    with pytest.raises(DuplicateSignal):
        router.submit(Entry("Buy", 0.01, "market", 79_600), "sig-1", last_price=80_000)
    assert router.resolve(link_id("sig-1")) is None
    assert risk.read_audit(router.state_dir)[0]["status"] == "not_found"


def test_duplicate_link_from_exchange_is_treated_as_accepted(router):
    router.client.fail["create_order"] = BybitError(110072, "OrderLinkedID is duplicate", "/v5/order/create")
    res = router.submit(Entry("Buy", 0.01, "market", 79_600), "sig-1", last_price=80_000)
    assert res["duplicate"] is True


def test_take_profit_is_reduce_only_postonly(router):
    router.take_profit("Buy", 0.01, 80_800.04, "sig-1")
    p = _created(router)[0]
    assert (p["side"], p["reduceOnly"], p["timeInForce"], p["price"]) == ("Sell", True, "PostOnly", "80800")


def test_router_marks_latency_stages(router, tmp_path):
    ticks = iter(range(0, 10**9, 1_000_000))
    rec = LatencyRecorder(tmp_path / "lat.jsonl", clock=lambda: next(ticks))
    tr = rec.start("sig-1", T0 + M, kind="market")
    tr.mark("t1")
    router.submit(Entry("Buy", 0.01, "market", 79_600), "sig-1", last_price=80_000, trace=tr)
    assert set(tr.mono_ns) == {"t1", "t4", "t5", "t5r"}


# 지연 기록 ---------------------------------------------------------------------------------------------

def test_latency_clocks_kept_separate_and_summarized(tmp_path):
    ticks = iter([0, 10_000_000, 30_000_000, 230_000_000] * 50)
    rec = LatencyRecorder(tmp_path / "lat.jsonl", clock=lambda: next(ticks))
    for i in range(50):
        tr = rec.start(f"k{i}", T0, kind="postonly")
        for s in ("t1", "t4", "t5", "t5r"):
            tr.mark(s)
        tr.exch("t6_new", T0 + 300 + i)
        tr.ref["close_lag_ms"] = 12.0
        rec.finish(f"k{i}", "postonly_rejected" if i % 10 == 0 else "new")
    with pytest.raises(ValueError):
        Trace("x", T0).mark("t6_exec")                         # 거래소 단계를 단조 시계로 찍을 수 없다
    rows = read_records(tmp_path / "lat.jsonl")
    assert rows[0]["decision_ms"] == 30 and rows[0]["ack_ms"] == 200 and rows[0]["new_ms"] == 300
    assert rows[0]["fill_ms"] is None and rows[0]["ref_close_lag_ms"] == 12.0
    s = summarize(rows)["base/postonly"]
    assert s["decision_ms"]["n"] == 50 and s["new_ms"]["p50"] == pytest.approx(324.5)
    assert s["postonly_reject_rate"] == pytest.approx(0.1)
    with (tmp_path / "lat.jsonl").open("a") as f:
        f.write('{"key": "trunc')                               # 비정상 종료로 잘린 행은 건너뛴다
    assert len(read_records(tmp_path / "lat.jsonl")) == 50


# WS -------------------------------------------------------------------------------------------------------

class FakeWS:
    def __init__(self, incoming, fail_after=False):
        self.sent, self._in, self._fail = [], list(incoming), fail_after

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, s):
        self.sent.append(json.loads(s))

    async def recv(self):
        return self._in.pop(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._in:
            return self._in.pop(0)
        if self._fail:
            raise OSError("connection reset")
        raise StopAsyncIteration


def test_auth_message_signature():
    m = auth_message("KEY", "SECRET", 1_700_000_010_000)
    expect = hmac.new(b"SECRET", b"GET/realtime1700000010000", hashlib.sha256).hexdigest()
    assert m == {"op": "auth", "args": ["KEY", 1_700_000_010_000, expect]}


def test_run_stream_auth_subscribe_dispatch_and_reconnect():
    conns = [FakeWS([json.dumps({"op": "auth", "success": True}), json.dumps({"topic": "order", "data": [1]})],
                    fail_after=True),
             FakeWS([json.dumps({"op": "auth", "success": True}), json.dumps({"topic": "order", "data": [2]})])]
    made, got, states, sleeps = [], [], [], []

    def connect(url):
        made.append(url)
        return conns[len(made) - 1]

    async def sleep(s):
        sleeps.append(s)

    asyncio.run(run_stream("wss://x", ["order"], got.append, auth=("K", "S"), clock_ms=lambda: 1_000,
                           on_state=states.append, connect=connect, max_connects=2, sleep=sleep))
    assert [m["data"] for m in got] == [[1], [2]]
    assert conns[0].sent[0]["op"] == "auth" and conns[0].sent[0]["args"][1] == 11_000
    assert conns[0].sent[1] == {"op": "subscribe", "req_id": "sub", "args": ["order"]}
    assert states == [True, False, True, False] and sleeps == [1]


def test_run_stream_auth_failure_raises():
    def connect(url):
        return FakeWS([json.dumps({"op": "auth", "success": False, "ret_msg": "invalid key"})])

    with pytest.raises(AuthError, match="invalid key"):
        asyncio.run(run_stream("wss://x", ["order"], lambda m: None, auth=("K", "S"), clock_ms=lambda: 0,
                               connect=connect, max_connects=3))


# 실연결 CLI 는 키 없이 공개 시세만 쓴다 ---------------------------------------------------------------------

def test_check_bars_report_uses_usable_bars_only():
    b = BarBuilder()
    bars = [Bar(T0, 1, 1, 1, 1, 1, 1, partial=True), Bar(T0 + M, 1, 1, 1, 1, 1, 1)]
    kl = [[str(T0 + M), "1", "1", "1", "1", "1", "0"]]
    rep = check_bars.report(bars, [10.0, 20.0], kl, RULES, b, rtt_ms=150)
    assert rep["usable_bars"] == 1 and rep["compared"] == 1 and rep["rate"] == 0
    assert rep["ref_close_lag_ms"]["p50"] == 15
    src = open(check_bars.__file__, encoding="utf-8").read()
    assert "create_order" not in src and "keys(" not in src


def test_check_bars_progress_file_for_dashboard(tmp_path):
    p = tmp_path / "progress" / "live.json"
    check_bars.write_progress(p, 12.34, 35, started=1000.0, note="닫은 봉 12개")
    j = json.loads(p.read_text(encoding="utf-8"))
    assert (j["done"], j["total"], j["unit"], j["task"]) == (12.3, 35, "분", "T-20261010-03")
    assert j["pid"] > 0 and j["updated"] >= j["started"]
    check_bars.write_progress(None, 1, 1, 0)                   # 경로 없으면 아무것도 안 쓴다
