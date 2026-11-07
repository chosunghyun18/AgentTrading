"""정규화 1분봉 parquet → 시세 DB 적재 + 왕복 검증. `python -m src.marketdata.sync [--start ...] [--end ...]`.

- 월 단위로 지우고 넣는다(한 달 = 한 트랜잭션) → 재실행해도 결과가 같다.
- 넣은 뒤 그 달을 DB 에서 다시 읽어 parquet 프레임과 **완전 일치**(dtype·값·순서)하는지 확인한다. 어긋나면 종료코드 1.
- 기본 표본 [2018-03-01, 2022-01-01) 밖(OOS 포함)은 거부한다 — OOS 데이터가 DB 로 새는 길을 막는다.
- 접속은 `AT_MARKET_DB_URL`(비밀번호는 출력하지 않는다). `AT_MARKET_SOURCE` 와 무관하게 parquet → DB 방향이다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import pandas as pd

from src.backtest.walkforward import check_sample_range
from src.ingest import store
from src.marketdata import db
from src.shared import settings


def months(start: date, end: date) -> list[tuple[date, date]]:
    """`start`~`end`(포함)를 달 경계로 자른 (첫날, 끝날) 목록."""
    out, s = [], start
    while s <= end:
        nxt = (pd.Timestamp(s) + pd.offsets.MonthBegin(1)).date()
        e = min(end, nxt - timedelta(days=1))
        out.append((s, e))
        s = e + timedelta(days=1)
    return out


def sync(eng, start: date, end: date, symbol: str, data_dir, log=print) -> int:
    """적재·검증. 불일치 달 수를 돌려준다(0 = 성공)."""
    s, e = check_sample_range(start, end + timedelta(days=1))
    db.create_tables(eng)
    bad = 0
    for m0, m1 in months(s.date(), (e - timedelta(days=1)).date()):
        src = store.load_bars(m0, m1, symbol, out_dir=data_dir)
        n = db.replace_bars(eng, src, m0, m1, symbol)
        back = db.load_bars(eng, m0, m1, symbol)
        try:
            pd.testing.assert_frame_equal(back, src, check_exact=True)
            ok = "일치"
        except AssertionError as err:
            bad += 1
            ok = f"불일치: {str(err).splitlines()[0]}"
        log(f"{symbol} {m0}~{m1} {n}행 {ok}")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.marketdata.sync", description=__doc__.split("\n")[0])
    ap.add_argument("--start", default="2018-03-01")
    ap.add_argument("--end", default="2021-12-31", help="끝날(포함)")
    ap.add_argument("--symbol", default=None, help="기본 AT_SYMBOL")
    a = ap.parse_args(argv)
    url = settings.get("AT_MARKET_DB_URL")
    if not url:
        print("AT_MARKET_DB_URL 이 비어 있다(.env)", file=sys.stderr)
        return 2
    symbol = a.symbol or settings.get("AT_SYMBOL")
    try:
        bad = sync(db.engine(url), date.fromisoformat(a.start), date.fromisoformat(a.end), symbol,
                   settings.path("AT_DATA_DIR"))
    except (ValueError, store.MissingDaysError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2
    print(f"완료 — DB {settings.db_name(url)!r}, 불일치 {bad}개월")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
