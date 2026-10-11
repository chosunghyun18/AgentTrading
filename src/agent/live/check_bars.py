"""공개 스트림 실연결로 로컬 1분봉을 만들고 REST kline 과 대조한다(J1 완료 기준 2). 주문·키 없음.

    .venv/bin/python -m src.agent.live.check_bars --minutes 35

결과: `data/agent/live_bars_check.json` — 가격·거래량 불일치율, 늦은 체결 수, 마감 판정 지연(참고값: 거래소 시각
추정이 섞임 → `ref_`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.agent import settings
from src.agent.bybit import LIVE_URL, Bybit
from src.agent.live.bars import BarBuilder, compare_klines, parse_public_trades
from src.agent.live.clock import ExchangeClock
from src.agent.live.ws import PUBLIC_URL, run_stream
from src.agent.risk import Rules

POLL_S = 0.005
PROGRESS = settings.ROOT / "data" / "logs" / "progress" / "live_bars_check.json"  # autodev 대시보드 진행 막대


def write_progress(path: Path | None, done: float, total: float, started: float, note: str = "") -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"title": "로컬 1분봉 실연결 대조", "task": "T-20261010-03", "unit": "분",
                               "done": round(done, 1), "total": total, "started": started, "updated": time.time(),
                               "pid": os.getpid(), "note": note}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


async def collect(symbol: str, minutes: float, clock: ExchangeClock, builder: BarBuilder,
                  url: str = PUBLIC_URL, progress: Path | None = None) -> tuple[list, list[float]]:
    bars, lags = [], []
    started, last_write = time.time(), 0.0

    def on_state(ok: bool) -> None:
        (builder.on_reconnect if ok else builder.on_disconnect)(clock.now_ms())

    def on_message(msg: dict) -> None:
        for t in parse_public_trades(msg):
            builder.on_trade(t)

    stream = asyncio.create_task(run_stream(url, [f"publicTrade.{symbol}"], on_message, on_state=on_state))
    loop = asyncio.get_running_loop()
    end = loop.time() + minutes * 60
    try:
        while loop.time() < end:
            now = clock.now_ms()
            for b in builder.poll(now):
                bars.append(b)
                lags.append(now - (b.start_ms + builder.interval))
            if stream.done():
                stream.result()  # 인증 오류 등은 그대로 올린다
            if time.time() - last_write >= 5:
                last_write = time.time()
                write_progress(progress, (last_write - started) / 60, minutes, started,
                               f"닫은 봉 {len(bars)}개 · 늦은 체결 {sum(builder.late.values())}건")
            await asyncio.sleep(POLL_S)
    finally:
        stream.cancel()
        try:
            await stream          # 연결을 닫고 나서 루프를 끝낸다
        except (asyncio.CancelledError, Exception):
            pass
    return bars, lags


def report(bars: list, lags: list[float], klines: list[list], rules: Rules, builder: BarBuilder, rtt_ms: float) -> dict:
    cmp = compare_klines(bars, klines, rules.tick, qty_eps=rules.qty_step / 2, late=builder.late)
    usable = [b for b in bars if b.usable]
    lag = np.percentile(lags, [50, 95, 99]).tolist() if lags else [None] * 3
    return {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bars": len(bars),
            "usable_bars": len(usable), "duplicates": builder.duplicates, "sync_rtt_ms": rtt_ms,
            "ref_close_lag_ms": dict(zip(("p50", "p95", "p99"), lag)), **cmp}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=float, default=35)
    ap.add_argument("--symbol", default=settings.symbol())
    ap.add_argument("--out", type=Path, default=settings.agent_dir() / "live_bars_check.json")
    ap.add_argument("--progress", type=Path, default=PROGRESS, help="진행 파일(autodev 대시보드)")
    a = ap.parse_args(argv)
    client = Bybit(LIVE_URL)  # 공개 시세만 — 키 없음(주문 불가)
    clock = ExchangeClock()
    rtt = clock.sync_best(lambda: int(client.server_time()["timeNano"]) // 1_000_000)
    rules = Rules.from_instrument(client.instrument(a.symbol))
    builder = BarBuilder()
    started = time.time()
    bars, lags = asyncio.run(collect(a.symbol, a.minutes, clock, builder, progress=a.progress))
    klines = client.klines(a.symbol, "1", limit=min(1000, int(a.minutes) + 10))
    rep = report(bars, lags, klines, rules, builder, rtt)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    write_progress(a.progress, a.minutes, a.minutes, started,
                   f"가격 불일치 {rep['price_mismatch']}/{rep['compared']} · 거래량 불일치 {rep['volume_mismatch']}")
    print(json.dumps({k: v for k, v in rep.items() if k != "details"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
