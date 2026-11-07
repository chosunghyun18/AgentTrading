"""시세 소스(parquet·db) 선택 규칙, DB 왕복·결측 일·재적재, OOS 거부. DB 는 SQLite URL 로 대신한다.

로컬 MySQL 실측은 `AT_TEST_MYSQL_URL` 이 있을 때만(`test_mysql_roundtrip`).
"""

import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src import marketdata
from src.ingest.store import MissingDaysError
from src.marketdata import db, sync
from src.shared import settings
from src.shared.schema import BARS_1M, validate

SYM = "XBTUSD"


def bars(day: str, n: int = 1440, symbol: str = SYM) -> pd.DataFrame:
    rng = np.random.default_rng(int(day.replace("-", "")))
    ts = pd.date_range(day, periods=n, freq="min", tz="UTC").as_unit("ns")
    close = 9000 + rng.normal(0, 5, n).cumsum()
    df = pd.DataFrame({
        "ts": ts, "symbol": pd.array([symbol] * n, dtype="string"),
        "open": close + 0.5, "high": close + 3.25, "low": close - 3.125, "close": close,
        "volume": rng.integers(0, 2**40, n), "volume_xbt": rng.random(n) * 1e3 + 1e-9,
        "trade_count": rng.integers(0, 5000, n), "buy_volume": rng.integers(0, 2**35, n),
        "sell_volume": rng.integers(0, 2**35, n),
    })
    return validate(df, BARS_1M)


@pytest.fixture
def eng(tmp_path):
    e = db.engine(f"sqlite:///{tmp_path / 'm.db'}")
    db.create_tables(e)
    return e


def _set(monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


# 설정 규칙 -----------------------------------------------------------------------------------------------

def test_default_is_parquet_local():
    src = settings.market_source()
    assert (src.env, src.kind, src.url) == ("local", "parquet", "")


@pytest.mark.parametrize("env,source,url,msg", [
    ("staging", "parquet", "", "AT_ENV"),
    ("local", "s3", "", "AT_MARKET_SOURCE"),
    ("local", "db", "", "비어"),
    ("dev", "parquet", "", "db 여야"),
    ("dev", "db", "mysql+pymysql://u:p@h/market_prod", "_dev"),
    ("prod", "db", "mysql+pymysql://u:p@h/market_dev", "_prod"),
    ("prod", "db", "mysql+pymysql://u:p@h/market", "_prod"),
    ("local", "db", "mysql+pymysql://u:p@h/market_prod", "운영 DB"),
])
def test_market_source_rules(monkeypatch, env, source, url, msg):
    _set(monkeypatch, AT_ENV=env, AT_MARKET_SOURCE=source, AT_MARKET_DB_URL=url)
    with pytest.raises(ValueError, match=msg):
        settings.market_source()


@pytest.mark.parametrize("env,name", [("dev", "market_dev"), ("prod", "market_prod"), ("local", "market")])
def test_market_source_ok_and_hides_password(monkeypatch, env, name):
    _set(monkeypatch, AT_ENV=env, AT_MARKET_SOURCE="db", AT_MARKET_DB_URL=f"mysql+pymysql://u:s3cret@h:3306/{name}?x=1")
    src = settings.market_source()
    assert src.kind == "db" and settings.db_name(src.url) == name
    assert "s3cret" not in repr(src) and "s3cret" not in str(src)


# DB 왕복 -------------------------------------------------------------------------------------------------

def test_roundtrip_exact(eng):
    src = pd.concat([bars("2019-06-01"), bars("2019-06-02")], ignore_index=True)
    assert db.replace_bars(eng, src, date(2019, 6, 1), date(2019, 6, 2), SYM) == 2880
    back = db.load_bars(eng, date(2019, 6, 1), date(2019, 6, 2), SYM)
    pd.testing.assert_frame_equal(back, src, check_exact=True)
    assert db.covered_days(eng, SYM) == [date(2019, 6, 1), date(2019, 6, 2)]


def test_missing_days_and_empty(eng, caplog):
    db.replace_bars(eng, bars("2019-06-01"), date(2019, 6, 1), date(2019, 6, 1), SYM)
    with pytest.raises(MissingDaysError) as ei:
        db.load_bars(eng, date(2019, 6, 1), date(2019, 6, 3), SYM)
    assert ei.value.days == [date(2019, 6, 2), date(2019, 6, 3)]
    part = db.load_bars(eng, date(2019, 6, 1), date(2019, 6, 3), SYM, allow_missing=True)
    assert len(part) == 1440 and "결측 일 2일" in caplog.text
    empty = db.load_bars(eng, date(2020, 1, 1), date(2020, 1, 1), SYM, allow_missing=True)
    assert len(empty) == 0 and validate(empty, BARS_1M) is empty
    with pytest.raises(ValueError, match="늦음"):
        db.load_bars(eng, date(2019, 6, 2), date(2019, 6, 1), SYM)


def test_replace_is_idempotent_and_scoped(eng):
    d1, d2 = bars("2019-06-01"), bars("2019-06-02")
    db.replace_bars(eng, pd.concat([d1, d2], ignore_index=True), date(2019, 6, 1), date(2019, 6, 2), SYM)
    other = bars("2019-06-01", symbol="ETHUSD")
    db.replace_bars(eng, other, date(2019, 6, 1), date(2019, 6, 1), "ETHUSD")
    db.replace_bars(eng, d1, date(2019, 6, 1), date(2019, 6, 1), SYM)  # 6/1 만 다시 — 6/2·ETHUSD 는 그대로
    pd.testing.assert_frame_equal(db.load_bars(eng, date(2019, 6, 1), date(2019, 6, 2), SYM),
                                  pd.concat([d1, d2], ignore_index=True))
    assert len(db.load_bars(eng, date(2019, 6, 1), date(2019, 6, 1), "ETHUSD")) == 1440


def test_replace_rejects_rows_outside_range(eng):
    with pytest.raises(ValueError, match="범위 밖"):
        db.replace_bars(eng, bars("2019-06-02"), date(2019, 6, 1), date(2019, 6, 1), SYM)


# 단일 창구 ----------------------------------------------------------------------------------------------

def test_load_bars_dispatch_db(monkeypatch, tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    e = db.engine(url)
    db.create_tables(e)
    db.replace_bars(e, bars("2019-06-01"), date(2019, 6, 1), date(2019, 6, 1), SYM)
    _set(monkeypatch, AT_MARKET_SOURCE="db", AT_MARKET_DB_URL=url)
    got = marketdata.load_bars(date(2019, 6, 1), date(2019, 6, 1), SYM, out_dir=tmp_path / "없는폴더")
    pd.testing.assert_frame_equal(got, bars("2019-06-01"))
    assert marketdata.covered_days(SYM) == [date(2019, 6, 1)]


def test_load_bars_dispatch_parquet(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(marketdata.store, "load_bars", lambda s, e, sym, *, out_dir, allow_missing: calls.append(out_dir))
    marketdata.load_bars(date(2019, 6, 1), date(2019, 6, 1), SYM)
    marketdata.load_bars(date(2019, 6, 1), date(2019, 6, 1), SYM, out_dir=tmp_path)
    assert calls == [settings.path("AT_DATA_DIR"), tmp_path]
    assert marketdata.covered_days(SYM) is None


# sync ---------------------------------------------------------------------------------------------------

def test_months():
    assert sync.months(date(2019, 1, 30), date(2019, 3, 2)) == [
        (date(2019, 1, 30), date(2019, 1, 31)), (date(2019, 2, 1), date(2019, 2, 28)), (date(2019, 3, 1), date(2019, 3, 2))]


def test_sync_roundtrip_and_oos_refused(monkeypatch, eng, tmp_path):
    src = {d: bars(d) for d in ("2019-01-31", "2019-02-01")}

    def fake(s, e, sym, *, out_dir):
        return pd.concat([v for k, v in src.items() if s <= date.fromisoformat(k) <= e], ignore_index=True)
    monkeypatch.setattr(sync.store, "load_bars", fake)
    lines = []
    assert sync.sync(eng, date(2019, 1, 31), date(2019, 2, 1), SYM, tmp_path, log=lines.append) == 0
    assert lines == [f"{SYM} 2019-01-31~2019-01-31 1440행 일치", f"{SYM} 2019-02-01~2019-02-01 1440행 일치"]
    with pytest.raises(ValueError, match="기본 표본"):
        sync.sync(eng, date(2021, 12, 31), date(2022, 1, 1), SYM, tmp_path, log=lines.append)


def test_sync_main_needs_url(monkeypatch, capsys):
    real = settings.get
    monkeypatch.setattr(sync.settings, "get", lambda k: "" if k == "AT_MARKET_DB_URL" else real(k))
    assert sync.main([]) == 2 and "AT_MARKET_DB_URL" in capsys.readouterr().err


@pytest.mark.skipif(not os.environ.get("AT_TEST_MYSQL_URL"), reason="AT_TEST_MYSQL_URL 없음(로컬 MySQL 실측 전용)")
def test_mysql_roundtrip():
    e = db.engine(os.environ["AT_TEST_MYSQL_URL"])
    db.create_tables(e)
    sym = "TESTSYM"
    src = bars("2019-06-01", symbol=sym)
    try:
        db.replace_bars(e, src, date(2019, 6, 1), date(2019, 6, 1), sym)
        pd.testing.assert_frame_equal(db.load_bars(e, date(2019, 6, 1), date(2019, 6, 1), sym), src, check_exact=True)
    finally:
        db.replace_bars(e, src.iloc[:0], date(2019, 6, 1), date(2019, 6, 1), sym)
