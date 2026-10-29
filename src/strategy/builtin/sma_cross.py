"""예제 전략 — 단순이동평균 교차. 검증된 전략이 아니다(`status: example`, 증거로 쓰지 않는다)."""

from __future__ import annotations

from src.strategy.base import Signal, Strategy


class SmaCross(Strategy):
    id = "sma_cross"
    version = "1"
    status = "example"
    entry_reason = "sma_cross"
    summary = "종가 SMA(fast) 가 SMA(slow) 를 위로 뚫으면 롱, 아래로 뚫으면 숏. 다음 봉 open 시장가."
    defaults = {"fast": 10, "slow": 30, "stop_pct": 1.0, "tp_r": 2.0, "max_hold": 120}

    def validate(self, p):
        if not 1 <= p["fast"] < p["slow"]:
            raise ValueError(f"1 ≤ fast < slow 여야 한다: fast={p['fast']}, slow={p['slow']}")
        if not 0 < p["stop_pct"] < 50:
            raise ValueError(f"stop_pct 는 (0, 50): {p['stop_pct']}")
        if p["tp_r"] <= 0 or p["max_hold"] < 1:
            raise ValueError("tp_r > 0, max_hold ≥ 1")

    def warmup(self, p):
        return p["slow"] + 1

    def on_bar(self, window, p):
        c = window["close"].to_numpy(dtype=float)
        f, s = p["fast"], p["slow"]
        now = c[-f:].mean() - c[-s:].mean()
        prev = c[-f - 1:-1].mean() - c[-s - 1:-1].mean()
        if prev <= 0 < now:
            side = "long"
        elif prev >= 0 > now:
            side = "short"
        else:
            return None
        return Signal(side, p["stop_pct"] / 100.0, p["tp_r"], p["max_hold"])


STRATEGY = SmaCross()
