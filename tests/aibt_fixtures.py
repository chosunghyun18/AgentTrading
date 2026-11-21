"""AI 백테스트 테스트용 합성 ETF 일봉 — sqlite `prices_daily`(MiroFishTrader 스키마) 를 만들어 URL 을 돌려준다. 실데이터 금지."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DDL = """CREATE TABLE prices_daily (symbol TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL,
    adj_close REAL NOT NULL, volume INTEGER, split_factor REAL DEFAULT 1.0, PRIMARY KEY (symbol, date))"""


def piecewise(n_up: int, n_down: int, n_rest: int, up=0.005, down=-0.01, rest=0.008, start=100.0) -> np.ndarray:
    """오름 → 내림 → 다시 오름 경로(결정적). 낙폭 트리거·이동평균 전환을 뚜렷하게 만든다."""
    r = np.concatenate([np.full(n_up, up), np.full(n_down, down), np.full(n_rest, rest)])
    return start * np.cumprod(1 + r)


def leveraged(base: np.ndarray, L: float) -> np.ndarray:
    r = np.diff(base) / base[:-1]
    return 100.0 * np.concatenate([[1.0], np.cumprod(1 + L * r)])


def series_set(n_up=60, n_down=50, n_rest=150, start="2020-01-01") -> pd.DataFrame:
    """A(기준)·B(3배)·C(상수 100)·D(A 와 같지만 60일 늦게 시작) — 날짜 × 종목 adj_close."""
    idx = pd.bdate_range(start, periods=n_up + n_down + n_rest)
    a = piecewise(n_up, n_down, n_rest)
    df = pd.DataFrame({"A": a, "B": leveraged(a, 3.0), "C": 100.0, "D": a}, index=idx)
    df.loc[df.index[:60], "D"] = np.nan
    return df


def write_sqlite(path: Path, prices: pd.DataFrame) -> str:
    con = sqlite3.connect(path)
    con.execute(DDL)
    rows = [(sym, d.strftime("%Y-%m-%d"), v, v, v, v, v, 0, 1.0)
            for sym in prices.columns for d, v in prices[sym].dropna().items()]
    con.executemany("INSERT INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return f"sqlite:///{path}"


def etf_like(path: Path) -> str:
    """페이지 테스트용 — 실제 종목 이름(QQQ·QLD·TQQQ)으로 같은 합성 경로."""
    base = series_set(80, 60, 400, start="2015-01-01")
    df = pd.DataFrame({"QQQ": base["A"], "QLD": leveraged(base["A"].to_numpy(), 2.0), "TQQQ": base["B"]}, index=base.index)
    return write_sqlite(path, df)
