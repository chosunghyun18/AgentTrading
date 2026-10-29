"""전략 무관 백테스트 엔진: 전략 + 1분봉 → 기존 `ROUNDTRIPS`(인버스 XBT).

비용·지표는 기존 `costs.apply_costs`·`metrics.summarize_net_run` 을 그대로 쓴다. 판정 경로(`backtest.run`)와 별개.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.contracts.inverse import Inverse
from src.shared.schema import ROUNDTRIPS, empty_frame, validate_roundtrips
from src.strategy.base import WINDOW_COLUMNS, Strategy
from src.strategy.sim import Sim

ONE_MIN = pd.Timedelta(minutes=1)


@dataclass(frozen=True)
class BatchResult:
    roundtrips: pd.DataFrame
    skipped_min_qty: int
    halted: bool
    signals: list  # [(신호 봉 ts, side)] — shadow 경로 동치 비교용


def feed(strategy: Strategy, p, sim: Sim, window: pd.DataFrame) -> object:
    """창 마지막 봉에서 신호를 평가해 sim 에 넣는다(바쁘면 부르지 않음). 넣은 신호 또는 None."""
    if sim.busy() or sim.blocked():
        return None
    sig = strategy.on_bar(window, p)
    if sig is not None:
        sim.submit(sig, window["ts"].iloc[-1])
    return sig


def batch_run(strategy: Strategy, params, bars: pd.DataFrame, *, risk_frac: float = 0.01,
              initial_equity: float = 1.0) -> BatchResult:
    p = strategy.params(params)
    w = strategy.warmup(p)
    if bars["symbol"].nunique() > 1:
        raise ValueError("한 심볼만")
    if len(bars) > 1 and not (bars["ts"].diff().iloc[1:] == ONE_MIN).all():
        raise ValueError("1분 연속 봉이어야 한다(결측·중복 없이)")
    frame = bars[list(WINDOW_COLUMNS)].reset_index(drop=True)
    sim = Sim(Inverse(), initial_equity, risk_frac)
    signals = []
    for j in range(len(frame)):
        sim.step(frame.iloc[j])
        if sim.halted:
            break
        if j + 1 >= w:
            sig = feed(strategy, p, sim, frame.iloc[j + 1 - w:j + 1].reset_index(drop=True))
            if sig is not None:
                signals.append((frame["ts"].iloc[j], sig.side))
    sim.finish()
    symbol = str(bars["symbol"].iloc[0]) if len(bars) else ""
    return BatchResult(_frame(sim.trades, strategy, p, symbol, risk_frac), sim.skipped, sim.halted, signals)


def _frame(trades, strategy: Strategy, p, symbol: str, risk_frac: float) -> pd.DataFrame:
    if not trades:
        return empty_frame(ROUNDTRIPS)
    t = pd.DataFrame(trades)
    df = pd.DataFrame({
        "strategy_id": strategy.key, "param_id": strategy.param_id(p),
        "trade_id": np.arange(len(t), dtype="int64"), "symbol": symbol, "side": t["side"],
        "signal_ts": t["signal_ts"], "entry_ts": t["entry_ts"], "entry_price": t["entry"],
        "exit_signal_ts": t["exit_signal_ts"], "exit_ts": t["exit_ts"], "exit_price": t["exit_price"],
        "qty": t["qty"], "stop_price": t["stop"], "tp_price": t["tp"],
        "entry_reason": strategy.entry_reason, "exit_reason": t["exit_reason"],
        "holding_min": 0.0, "equity_before": t["equity_before"], "notional_usd": t["qty"].astype(float),
        "leverage": (t["qty"] / t["entry"]) / t["equity_before"], "risk_pct": risk_frac * 100.0,
        "size_capped": t["capped"].astype(bool), "gross_pnl_xbt": t["pnl"], "gross_ret": t["pnl"] / t["equity_before"],
    })
    for col in ("signal_ts", "entry_ts", "exit_signal_ts", "exit_ts"):
        df[col] = pd.to_datetime(df[col], utc=True)
    df["holding_min"] = (df["exit_ts"] - df["entry_ts"]) / ONE_MIN
    df = df[ROUNDTRIPS.column_names].astype(ROUNDTRIPS.dtypes)
    return validate_roundtrips(df)
