"""시세 DB(SQLAlchemy Core) — `bars_1m` 테이블 정의·읽기·쓰기. ORM 은 쓰지 않는다.

- 열·dtype 은 `shared.schema.BARS_1M` 그대로. 시각은 UTC naive `DATETIME(6)`(MySQL) 로 저장하고 읽을 때 UTC ns 로 되돌린다.
- 결측 일 = 그 UTC 일에 행이 하나도 없는 날(parquet 의 "일 파일 없음"과 같은 뜻 — 적재 원본 1,402일은 모두 1,440행).
- 같은 서버의 다른 테이블(MiroFishTrader `prices_*`)은 건드리지 않는다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from functools import lru_cache

import pandas as pd
from sqlalchemy import (BigInteger, Column, DateTime, Float, MetaData, String, Table, create_engine, delete,
                        func, select)
from sqlalchemy.dialects.mysql import DATETIME as MYSQL_DATETIME
from sqlalchemy.dialects.mysql import DOUBLE as MYSQL_DOUBLE
from sqlalchemy.engine import Engine

from src.ingest.normalize import _days
from src.ingest.store import MissingDaysError
from src.shared.schema import BARS_1M, empty_frame, validate

log = logging.getLogger(__name__)
metadata = MetaData()
_TS = DateTime().with_variant(MYSQL_DATETIME(fsp=6), "mysql")
# asdecimal=False 필수 — MySQL DOUBLE 기본은 Decimal(소수 10자리 반올림)로 돌려줘 1 ulp 가 어긋난다
_F = Float(precision=53).with_variant(MYSQL_DOUBLE(asdecimal=False), "mysql")

bars_1m = Table(
    "bars_1m", metadata,
    Column("symbol", String(20), primary_key=True),
    Column("ts", _TS, primary_key=True),
    Column("open", _F, nullable=False),
    Column("high", _F, nullable=False),
    Column("low", _F, nullable=False),
    Column("close", _F, nullable=False),
    Column("volume", BigInteger, nullable=False),
    Column("volume_xbt", _F, nullable=False),
    Column("trade_count", BigInteger, nullable=False),
    Column("buy_volume", BigInteger, nullable=False),
    Column("sell_volume", BigInteger, nullable=False),
)
COLS = [c.name for c in BARS_1M.columns]
_INTS = [c.name for c in BARS_1M.columns if c.dtype == "int64"]


@lru_cache(maxsize=4)
def engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def create_tables(eng: Engine) -> None:
    metadata.create_all(eng, tables=[bars_1m])


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    if start > end:
        raise ValueError(f"start 가 end 보다 늦음: {start} > {end}")
    return datetime.combine(start, time()), datetime.combine(end + timedelta(days=1), time())


def to_frame(df: pd.DataFrame) -> pd.DataFrame:
    """DB 에서 읽은 행 → BARS_1M 스키마 프레임(UTC ns·string·int64)."""
    if df.empty:
        return empty_frame(BARS_1M)
    df = df[COLS].copy()
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize("UTC").dt.as_unit("ns")
    df["symbol"] = df["symbol"].astype("string")
    df[_INTS] = df[_INTS].astype("int64")
    df = df.astype({c: "float64" for c in COLS if c not in _INTS and c not in ("ts", "symbol")})
    return validate(df.sort_values("ts", kind="stable", ignore_index=True), BARS_1M)


def load_bars(eng: Engine, start: date, end: date, symbol: str = "XBTUSD", *,
              allow_missing: bool = False) -> pd.DataFrame:
    """`ingest.store.load_bars` 와 같은 약속: `start`~`end`(포함), 결측 일이 있으면 기본 `MissingDaysError`."""
    lo, hi = _bounds(start, end)
    q = (select(bars_1m).where(bars_1m.c.symbol == symbol, bars_1m.c.ts >= lo, bars_1m.c.ts < hi)
         .order_by(bars_1m.c.ts))
    with eng.connect() as con:
        df = to_frame(pd.read_sql(q, con))
    have = set(df["ts"].dt.date)
    gaps = [d for d in _days(start, end) if d not in have]
    if gaps:
        err = MissingDaysError("bars_1m", symbol, gaps)
        if not allow_missing:
            raise err
        log.warning("%s — 있는 날만 읽음", err)
    return df


def covered_days(eng: Engine, symbol: str = "XBTUSD") -> list[date]:
    day = func.date(bars_1m.c.ts)
    with eng.connect() as con:
        rows = con.execute(select(day).where(bars_1m.c.symbol == symbol).group_by(day).order_by(day)).scalars()
        return [d if isinstance(d, date) else date.fromisoformat(str(d)) for d in rows]


def replace_bars(eng: Engine, df: pd.DataFrame, start: date, end: date, symbol: str, chunk: int = 5000) -> int:
    """`start`~`end`(포함) 의 `symbol` 행을 지우고 `df` 로 채운다 — 한 트랜잭션(중간 실패 시 이전 상태 유지)."""
    lo, hi = _bounds(start, end)
    df = validate(df, BARS_1M)
    if len(df) and ((df["symbol"] != symbol).any() or df["ts"].min() < pd.Timestamp(lo, tz="UTC")
                    or df["ts"].max() >= pd.Timestamp(hi, tz="UTC")):
        raise ValueError(f"df 가 {symbol} {start}~{end} 범위 밖 행을 담고 있다")
    out = df[COLS].copy()
    out["ts"] = pd.Series([t.to_pydatetime() for t in out["ts"].dt.tz_convert(None)], index=out.index, dtype=object)
    out["symbol"] = out["symbol"].astype(object)
    rows = out.to_dict("records")
    with eng.begin() as con:
        con.execute(delete(bars_1m).where(bars_1m.c.symbol == symbol, bars_1m.c.ts >= lo, bars_1m.c.ts < hi))
        for i in range(0, len(rows), chunk):
            con.execute(bars_1m.insert(), rows[i:i + chunk])
    return len(rows)

