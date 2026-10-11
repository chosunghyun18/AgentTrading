"""공개 체결 스트림(`publicTrade`) → 로컬 1분봉.

- 봉 [start, start+60 s) 은 거래소 시각 `start + 60 000 + CLOSE_DELAY_MS` 가 지나면 `poll()` 이 닫는다.
  kline 스트림은 푸시 주기가 1–60 s 라 마감 판정에 못 쓴다(설계 5절 ①). 봉 귀속은 수신 시각이 아니라 체결 시각 `T`.
- 닫힌 구간에 뒤늦게 도착한 체결은 봉을 고치지 않고 `late` 로 센다(실시간 판단과 같은 값을 남기기 위해).
- 재연결 때 다시 오는 체결은 체결 ID `i` 로 거른다.
- 거래 없는 분은 직전 종가로 평평한 봉(거래량 0)을 만든다 — Bybit kline 과 같은 규칙.
- 첫 봉은 분 중간부터 받기 시작했을 수 있어 `partial`, WS 가 끊긴 구간에 걸친 봉은 `suspect` 로 표시한다.
  둘 다 신호 평가에 쓰지 않는다. `fill_from_klines()` 로 REST kline 값으로 바꿔 보충한다.
- `compare_klines()` 로 REST kline 과 사후 대조한다(Bybit kline 은 open = 직전 종가 규약이라 맞춰서 비교).
  가격(OHLC)과 거래량 불일치는 따로 센다
  (블록·RPI 체결이 kline 거래량에 어떻게 들어가는지 미확인).
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass

INTERVAL_MS = 60_000
CLOSE_DELAY_MS = 50
SEEN_IDS = 20_000  # 중복 제거용으로 기억하는 최근 체결 ID 수


@dataclass(frozen=True)
class Trade:
    ts_ms: int      # 거래소 체결 시각
    price: float
    size: float
    trade_id: str = ""


@dataclass(frozen=True)
class Bar:
    start_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int
    partial: bool = False   # 받기 시작한 첫 봉(분 중간부터)
    suspect: bool = False   # WS 끊김 구간에 걸침
    source: str = "ws"      # ws | rest(보충)

    @property
    def usable(self) -> bool:
        """신호 평가에 써도 되는 봉."""
        return not (self.partial or self.suspect)


def parse_public_trades(msg: dict) -> list[Trade]:
    """`publicTrade.<심볼>` 메시지 → 체결 목록. 필드: T(ms)·p(가격)·v(수량)·i(체결 ID). 가격·수량은 float."""
    return [Trade(int(d["T"]), float(d["p"]), float(d["v"]), str(d.get("i", ""))) for d in msg.get("data") or []]


class _Acc:
    __slots__ = ("open", "high", "low", "close", "volume", "trades")

    def __init__(self, t: Trade):
        self.open = self.high = self.low = self.close = t.price
        self.volume, self.trades = t.size, 1

    def add(self, t: Trade) -> None:
        self.high, self.low = max(self.high, t.price), min(self.low, t.price)
        self.close = t.price
        self.volume += t.size
        self.trades += 1


class BarBuilder:
    def __init__(self, interval_ms: int = INTERVAL_MS, close_delay_ms: int = CLOSE_DELAY_MS):
        self.interval, self.delay = interval_ms, close_delay_ms
        self._open: dict[int, _Acc] = {}
        self._next: int | None = None        # 다음에 닫을 봉의 시작 시각(이보다 이른 구간은 닫힘)
        self._first: int | None = None
        self._last_close: float | None = None
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._gaps: list[list[float]] = []    # [끊긴 시각, 다시 붙은 시각(None=아직 끊김)]
        self.late = Counter()                 # 봉 시작 → 늦게 도착한 체결 수
        self.duplicates = 0

    def _start(self, ts_ms: int) -> int:
        return ts_ms - ts_ms % self.interval

    def on_disconnect(self, exch_ms: float) -> None:
        if not self._gaps or self._gaps[-1][1] is not None:
            self._gaps.append([exch_ms, None])

    def on_reconnect(self, exch_ms: float) -> None:
        if self._gaps and self._gaps[-1][1] is None:
            self._gaps[-1][1] = exch_ms

    def _in_gap(self, start: int) -> bool:
        end = start + self.interval
        return any(g0 < end and (g1 is None or g1 > start) for g0, g1 in self._gaps)

    def on_trade(self, t: Trade) -> None:
        if t.trade_id:
            if t.trade_id in self._seen:
                self.duplicates += 1
                return
            self._seen[t.trade_id] = None
            if len(self._seen) > SEEN_IDS:
                self._seen.popitem(last=False)
        start = self._start(t.ts_ms)
        if self._next is None:
            self._next = self._first = start
        if start < self._next:
            self.late[start] += 1
            return
        acc = self._open.get(start)
        if acc is None:
            self._open[start] = _Acc(t)
        else:
            acc.add(t)

    def poll(self, now_exch_ms: float) -> list[Bar]:
        """마감 시각이 지난 봉을 시간순으로 닫아 돌려준다."""
        out: list[Bar] = []
        while self._next is not None and now_exch_ms >= self._next + self.interval + self.delay:
            start = self._next
            acc = self._open.pop(start, None)
            flags = {"partial": start == self._first, "suspect": self._in_gap(start)}
            if acc is not None:
                bar = Bar(start, acc.open, acc.high, acc.low, acc.close, acc.volume, acc.trades, **flags)
            else:
                c = self._last_close
                bar = Bar(start, c, c, c, c, 0.0, 0, **flags)
            self._last_close = bar.close
            self._next = start + self.interval
            out.append(bar)
        self._gaps = [g for g in self._gaps if g[1] is None or g[1] > (self._next or 0)]
        return out


def bar_from_kline(k: list) -> Bar:
    o, h, lo, c, v = (float(x) for x in k[1:6])
    return Bar(int(k[0]), o, h, lo, c, v, -1, source="rest")


def fill_from_klines(bars: list[Bar], klines: list[list]) -> list[Bar]:
    """미완성·의심 봉을 REST kline 값으로 바꾼다(없으면 그대로)."""
    rest = {int(k[0]): k for k in klines}
    return [bar_from_kline(rest[b.start_ms]) if not b.usable and b.start_ms in rest else b for b in bars]


def to_kline_convention(b: Bar, prev_close: float | None) -> tuple[float | None, float, float, float]:
    """Bybit kline 규약으로 바꾼 (open, high, low, close).

    Bybit kline 의 open 은 첫 체결가가 아니라 직전 봉 종가다(2026-10-10 REST 999/999 확인). 그래서 high·low 에도
    직전 종가가 포함된다. 로컬 봉은 백테스트 봉(`src/ingest/bars.py`, open = 첫 체결)과 같은 규약을 유지하고,
    대조할 때만 이 함수로 맞춘다. 직전 종가를 모르면 open 은 None(비교 안 함).
    """
    if prev_close is None:
        return None, b.high, b.low, b.close
    return prev_close, max(b.high, prev_close), min(b.low, prev_close), b.close


def compare_klines(bars: list[Bar], klines: list[list], tick: float, qty_eps: float = 1e-9,
                   late: Counter | None = None) -> dict:
    """로컬 봉 vs REST kline(`[startMs, o, h, l, c, volume, turnover]`).

    로컬 봉을 kline 규약(`to_kline_convention`)으로 바꿔 비교한다. 가격은 호가 단위 절반, 거래량은 `qty_eps` 를
    넘게 다르면 불일치. 신호에 못 쓰는 봉(partial·suspect)은 뺀다. 가격 불일치율(`rate`)과 거래량 불일치율
    (`volume_rate`)은 따로 낸다.
    """
    rest = {int(k[0]): k for k in klines}
    compared, details, missing, price_bad, vol_bad = 0, [], 0, 0, 0
    prev: Bar | None = None
    for b in bars:
        prev_close = prev.close if prev is not None and prev.start_ms == b.start_ms - INTERVAL_MS else None
        prev = b
        if not b.usable or b.source != "ws":
            continue
        k = rest.get(b.start_ms)
        if k is None:
            missing += 1
            continue
        compared += 1
        r = bar_from_kline(k)
        mine = dict(zip(("open", "high", "low", "close"), to_kline_convention(b, prev_close)))
        diffs = {name: (v, getattr(r, name)) for name, v in mine.items()
                 if v is not None and abs(v - getattr(r, name)) > tick / 2}
        price_bad += bool(diffs)
        if abs(b.volume - r.volume) > qty_eps:
            diffs["volume"] = (b.volume, r.volume)
            vol_bad += 1
        if diffs:
            details.append({"start_ms": b.start_ms, "late": (late or {}).get(b.start_ms, 0), **diffs})
    rate = (lambda n: n / compared if compared else None)  # noqa: E731
    return {"compared": compared, "price_mismatch": price_bad, "rate": rate(price_bad),
            "volume_mismatch": vol_bad, "volume_rate": rate(vol_bad), "missing_rest": missing,
            "late_trades": sum((late or {}).values()), "details": details}
