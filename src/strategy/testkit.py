"""전략 계약 테스트 3종(스키마·미래 누수·결정성) + 결정적 랜덤워크 1분봉."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas.testing as pdt

from src.shared.schema import validate_roundtrips
from src.strategy.base import Strategy
from src.strategy.engine import batch_run


def synthetic_bars(n: int = 3000, seed: int = 7, start="2019-06-01", symbol="XBTUSD",
                   price: float = 8000.0, vol: float = 0.002) -> pd.DataFrame:
    """BARS_1M 열을 갖춘 1분 연속 랜덤워크 봉(시드 고정)."""
    rng = np.random.default_rng(seed)
    close = price * np.exp(np.cumsum(rng.normal(0, vol, n)))
    open_ = np.concatenate([[price], close[:-1]])
    spread = np.abs(rng.normal(0, vol / 2, n)) * close
    return pd.DataFrame({
        "ts": pd.date_range(start, periods=n, freq="1min", tz="UTC"),
        "symbol": pd.Series([symbol] * n, dtype="string"), "open": open_, "high": np.maximum(open_, close) + spread,
        "low": np.minimum(open_, close) - spread, "close": close,
        "volume": np.full(n, 100, dtype="int64"), "volume_xbt": np.full(n, 0.01),
        "trade_count": np.full(n, 10, dtype="int64"), "buy_volume": np.full(n, 50, dtype="int64"),
        "sell_volume": np.full(n, 50, dtype="int64"),
    })


def check_schema(s: Strategy, p, bars) -> None:
    validate_roundtrips(batch_run(s, p, bars).roundtrips, strict=True)


def check_no_lookahead(s: Strategy, p, bars, cut: int | None = None) -> None:
    """앞부분만 준 run 의 확정 거래(end_of_data 제외)는 전체 run 의 앞 거래와 같아야 한다."""
    cut = cut or len(bars) * 2 // 3
    full = batch_run(s, p, bars).roundtrips
    part = batch_run(s, p, bars.iloc[:cut]).roundtrips
    part = part[part["exit_reason"] != "end_of_data"].reset_index(drop=True)
    pdt.assert_frame_equal(part, full.iloc[:len(part)].reset_index(drop=True))


def check_determinism(s: Strategy, p, bars) -> None:
    pdt.assert_frame_equal(batch_run(s, p, bars).roundtrips, batch_run(s, p, bars).roundtrips)


def check_all(s: Strategy, params=None, bars=None) -> list[str]:
    """실패한 검사 이름 목록(빈 목록 = 통과)."""
    bars = synthetic_bars() if bars is None else bars
    p = s.params(params)
    failed = []
    for name, fn in (("schema", check_schema), ("no_lookahead", check_no_lookahead),
                     ("determinism", check_determinism)):
        try:
            fn(s, p, bars)
        except AssertionError:
            failed.append(name)
        except ValueError as e:
            failed.append(f"{name}: {e}")
    return failed
