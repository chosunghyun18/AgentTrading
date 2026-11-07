"""시세(1분봉) 읽기 단일 창구 — `AT_MARKET_SOURCE` 에 따라 parquet 폴더 또는 시세 DB 에서 읽는다.

    from src.marketdata import load_bars
    bars = load_bars(date(2019, 6, 1), date(2019, 6, 30))   # 반환·결측 일 규칙은 `ingest.store.load_bars` 와 같다

- 쓰는 곳: 플랫폼 `at backtest`, 화면 `ui.data.load_bars_guarded`. **판정 경로(`backtest/run.py`·`run_c1.py`·analysis)는
  parquet(`ingest.store`) 그대로** — 판정 경로 바이트 불변 원칙.
- 표본(OOS) 가드는 여기서 하지 않는다. 호출부(`check_sample_range`)가 그대로 맡는다.
- DB 경로는 `src.marketdata.db`(SQLAlchemy Core, optional extra `db`) — parquet 모드에서는 import 하지 않는다.
설계: Obsidian `Projects/work/AgentTrading/task/market-db-source.md`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.ingest import store
from src.shared import settings


def load_bars(start: date, end: date, symbol: str = "XBTUSD", *, out_dir: Path | None = None,
              allow_missing: bool = False) -> pd.DataFrame:
    """`start`~`end`(포함) 1분봉. parquet 모드의 `out_dir` 기본은 `AT_DATA_DIR`. db 모드에서는 `out_dir` 를 무시한다."""
    src = settings.market_source()
    if src.kind == "db":
        from src.marketdata import db
        return db.load_bars(db.engine(src.url), start, end, symbol, allow_missing=allow_missing)
    return store.load_bars(start, end, symbol, out_dir=out_dir or settings.path("AT_DATA_DIR"),
                           allow_missing=allow_missing)


def covered_days(symbol: str = "XBTUSD") -> list[date] | None:
    """db 모드에서 1분봉이 있는 UTC 일(오름차순). parquet 모드면 None(호출부가 manifest 를 본다)."""
    src = settings.market_source()
    if src.kind != "db":
        return None
    from src.marketdata import db
    return db.covered_days(db.engine(src.url), symbol)
