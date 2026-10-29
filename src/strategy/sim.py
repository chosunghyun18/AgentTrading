"""봉 단위 체결 시뮬레이터 — 백테스트 엔진과 shadow 브로커가 **같은 코드**를 쓴다(경로 동치).

규칙은 v1(`synthetic.generate_run`) 과 같다: 신호 봉 다음 봉 open 시장가 진입(사이징은 진입가 기준),
봉 내 손절 우선·갭은 진입 봉 이후만(`_intrabar_exit`), `max_hold` 봉째에 표시 → 다음 봉 open 청산(`time`),
데이터 끝은 `finish()` 가 마지막 close 로 청산(`end_of_data`). 자본 ≤ 0 이면 halted.
"""

from __future__ import annotations

import math

from src.analysis.synthetic import _intrabar_exit
from src.strategy.base import Signal


class Sim:
    def __init__(self, contract, equity: float, risk_frac: float = 0.01, max_exposure: float | None = None):
        if not 0 < risk_frac <= 0.05:
            raise ValueError(f"risk_frac 은 (0, 0.05]: {risk_frac!r}")
        self.contract, self.equity, self.risk_frac = contract, float(equity), float(risk_frac)
        self.size_kw = {} if max_exposure is None else {"max_exposure": float(max_exposure)}
        self.j = -1
        self.prev_ts = None
        self.last = None
        self.pending: tuple[Signal, object] | None = None  # (신호, 신호 봉 ts)
        self.pos: dict | None = None
        self.trades: list[dict] = []
        self.skipped = 0
        self.halted = False

    def busy(self) -> bool:
        return self.pos is not None or self.pending is not None

    def blocked(self) -> bool:
        """신호를 받지 않는 상태: halted, 또는 시간 청산 표시(다음 봉 open 청산 대기)."""
        return self.halted or (self.pos is not None and self.pos["time_exit"])

    def submit(self, sig: Signal, signal_ts) -> None:
        if self.busy() or self.blocked():
            raise RuntimeError("포지션·진입 대기 중에는 신호를 받지 않는다")
        self.pending = (sig, signal_ts)

    def _exit(self, reason, price, ts, exit_signal_ts) -> dict:
        pos = self.pos
        pnl = self.contract.pnl(pos["side"], pos["qty"], pos["entry"], float(price))
        trade = {**pos, "exit_reason": reason, "exit_price": float(price), "exit_ts": ts,
                 "exit_signal_ts": exit_signal_ts, "pnl": pnl}
        del trade["time_exit"]
        self.trades.append(trade)
        self.equity = pos["equity_before"] + pnl
        self.pos = None
        if self.equity <= 0:
            self.halted = True
        return {"kind": "exit", **{k: trade[k] for k in ("side", "qty", "entry", "exit_price", "exit_reason", "pnl")}}

    def step(self, bar) -> list[dict]:
        """닫힌 봉 1개(dict·Series: ts, open, high, low, close). 이 봉에서 일어난 이벤트 목록."""
        if self.halted:
            return []
        self.j += 1
        ts, o, h, lo = bar["ts"], float(bar["open"]), float(bar["high"]), float(bar["low"])
        events: list[dict] = []
        if self.pending is not None:
            sig, sig_ts = self.pending
            self.pending = None
            qty, capped = self.contract.size(self.equity, o, sig.side, sig.stop_frac, self.risk_frac, **self.size_kw)
            if qty <= 0 or (self.contract.name == "inverse" and qty < 1):
                self.skipped += 1
                events.append({"kind": "skip", "reason": "min_qty", "side": sig.side})
            else:
                s = sig.stop_frac
                d = 1 if sig.side == "long" else -1
                self.pos = {"side": sig.side, "e": self.j, "entry": o, "qty": qty, "capped": capped,
                            "stop": o * (1 - d * s), "tp": o * (1 + d * sig.tp_r * s) if sig.tp_r else math.nan,
                            "max_hold": sig.max_hold, "equity_before": self.equity, "signal_ts": sig_ts,
                            "entry_ts": ts, "time_exit": False}
                events.append({"kind": "entry", "side": sig.side, "qty": qty, "entry": o,
                               "stop": self.pos["stop"], "tp": self.pos["tp"]})
        if self.pos is not None:
            pos = self.pos
            if pos["time_exit"]:
                events.append(self._exit("time", o, ts, self.prev_ts))
            else:
                hit = _intrabar_exit(pos["side"], o, h, lo, pos["stop"], pos["tp"], gap_ok=self.j > pos["e"])
                if hit is not None:
                    events.append(self._exit(hit[0], hit[1], ts, None))
                elif pos["max_hold"] and self.j == pos["e"] + pos["max_hold"] - 1:
                    pos["time_exit"] = True
        self.prev_ts, self.last = ts, bar
        return events

    def finish(self) -> list[dict]:
        """데이터 끝: 진입 대기는 버리고, 열린 포지션은 마지막 close 로 청산."""
        self.pending = None
        if self.pos is None or self.last is None:
            return []
        return [self._exit("end_of_data", float(self.last["close"]), self.last["ts"], self.last["ts"])]
