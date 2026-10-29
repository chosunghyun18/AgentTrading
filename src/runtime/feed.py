"""REST kline 1분 폴링 → 닫힌 봉만, 오래된 순, 중복 없이. 조회 전용 공개 클라이언트로 부른다(키 없음)."""

from __future__ import annotations

import time

import pandas as pd

INTERVAL_MS = 60_000


def closed_bars(rows, now_ms: int) -> list[dict]:
    """Bybit kline 행([startMs, o, h, l, c, vol, turnover], 최신 → 과거) → 닫힌 봉 dict 목록(과거 → 최신)."""
    seen: dict[int, dict] = {}
    for r in rows:
        start = int(r[0])
        if start + INTERVAL_MS > now_ms:
            continue  # 아직 만들어지는 봉
        seen[start] = {"ts": pd.Timestamp(start, unit="ms", tz="UTC"), "open": float(r[1]), "high": float(r[2]),
                       "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
    return [seen[k] for k in sorted(seen)]


def run_loop(rt, client, symbol: str, *, max_bars: int | None = None, sleep=time.sleep,
             now_ms=lambda: int(time.time() * 1000), limit: int = 200) -> int:
    """창을 과거 봉으로 채우고, 이후 새로 닫힌 봉마다 `rt.on_bar`. 처리한 새 봉 수."""
    history = closed_bars(client.klines(symbol, "1", limit), now_ms())
    rt.prime(history)
    n = 0
    while rt.status is None and (max_bars is None or n < max_bars):
        wait = INTERVAL_MS - now_ms() % INTERVAL_MS + 2_000  # 다음 분 + 2초
        sleep(wait / 1000)
        for bar in closed_bars(client.klines(symbol, "1", 5), now_ms()):
            if rt.last_ts is None or bar["ts"] > rt.last_ts:
                rt.on_bar(bar)
                n += 1
                if rt.status is not None or (max_bars is not None and n >= max_bars):
                    break
    return n
