"""정규화 일별 parquet(`trades`·`bars_1m`·`fills`) 구간 로더와 결측 일 판정 (읽기 전용).

경로 규칙·기본 디렉터리는 `src.ingest.normalize`(`trades_path`·`bars_path`·`DEFAULT_OUT_DIR`)
한 곳에서만 정의되고 여기서는 가져다 쓴다.
설계 근거(단일 기준): Obsidian `Projects/work/AgentTrading/design/phase1-ingest-schema.md` "저장 형식"

    from datetime import date
    from src.ingest.store import load_bars, load_trades, missing_days
    bars = load_bars(date(2019, 6, 1), date(2019, 6, 30))            # 결측 일이 있으면 MissingDaysError
    gaps = missing_days(date(2019, 6, 1), date(2019, 6, 30))         # [date, ...]
    part = load_bars(date(2019, 6, 1), date(2019, 6, 30), allow_missing=True)  # WARNING + 있는 날만

- 구간은 `start`~`end` 양끝 포함 UTC 일. `start > end` 면 ValueError.
- 결측 일 = 그 테이블의 일별 parquet 파일이 없는 날(manifest 는 보지 않는다).
- 결측 일이 있으면 기본은 `MissingDaysError`(속성 `days`). 끊긴 구간을 모르고 이어 붙이면
  "구간이 끊기면 prev_close 를 넘기지 않는다" 규칙을 어긴 가짜 연속 시계열이 되기 때문이다.
  `allow_missing=True` 면 WARNING 을 남기고 있는 날만 읽는다. 이때 반환 프레임은 끊긴 구간을
  포함할 수 있으니 소비자가 `missing_days` 로 연속 구간을 나눠 써야 한다.
- 반환: 일별 파일을 날짜순으로 이어 붙여 `ts` 기준 stable 정렬한 뒤 `schema.validate` 를 통과한
  프레임(인덱스 0..n-1). 읽을 파일이 없으면 스키마 dtype 의 0행 프레임.
- 전체 구간을 메모리로 읽는다(지연·청크 로딩은 후속 작업).
- `load_fills`(행동 체결, 기본 source=aoa)는 결측 일 오류를 내지 않는다 — 거래가 없는 날은 일 파일이
  없는 것이 정상이다. 대신 `_manifest.json` 이 없으면(정규화 미실행) FileNotFoundError, 구간이 manifest
  일 범위 밖으로 나가면 WARNING. 경로는 `src.ingest.aoa.fills_path`.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.ingest import aoa
from src.ingest.normalize import DEFAULT_OUT_DIR, _days, bars_path, trades_path
from src.shared.schema import BARS_1M, FILLS, TRADES, TableSchema, empty_frame, validate

log = logging.getLogger(__name__)

_TABLES: dict[str, tuple[TableSchema, object]] = {
    "trades": (TRADES, trades_path),
    "bars_1m": (BARS_1M, bars_path),
}


class MissingDaysError(ValueError):
    """구간 안에 정규화 일별 파일이 없는 날이 있다. `days` 에 결측 일 목록(오름차순)."""

    def __init__(self, table: str, symbol: str, days: list[date]):
        self.table = table
        self.symbol = symbol
        self.days = list(days)
        shown = ", ".join(map(str, self.days[:10])) + (" ..." if len(self.days) > 10 else "")
        super().__init__(f"[{table}] {symbol} 결측 일 {len(self.days)}일: {shown}")


def _table(table: str) -> tuple[TableSchema, object]:
    try:
        return _TABLES[table]
    except KeyError:
        raise ValueError(f"알 수 없는 table: {table!r} (허용 {sorted(_TABLES)})") from None


def _check_range(start: date, end: date) -> None:
    if start > end:
        raise ValueError(f"start 가 end 보다 늦음: {start} > {end}")


def missing_days(start: date, end: date, symbol: str = "XBTUSD", *, table: str = "bars_1m",
                 out_dir: Path = DEFAULT_OUT_DIR) -> list[date]:
    """`start`~`end`(포함) 중 `table` 의 일별 parquet 가 없는 날(오름차순)."""
    _, path_of = _table(table)
    _check_range(start, end)
    out_dir = Path(out_dir)
    return [d for d in _days(start, end) if not path_of(out_dir, symbol, d).is_file()]


def _load(table: str, start: date, end: date, symbol: str, out_dir: Path,
          allow_missing: bool) -> pd.DataFrame:
    schema, path_of = _table(table)
    _check_range(start, end)
    out_dir = Path(out_dir)
    gaps = missing_days(start, end, symbol, table=table, out_dir=out_dir)
    if gaps:
        err = MissingDaysError(table, symbol, gaps)
        if not allow_missing:
            raise err
        log.warning("%s — 있는 날만 읽음", err)

    missing = set(gaps)
    frames = [pd.read_parquet(path_of(out_dir, symbol, d), engine="pyarrow")
              for d in _days(start, end) if d not in missing]
    nonempty = [f for f in frames if len(f)]
    if nonempty:
        df = pd.concat(nonempty, ignore_index=True)
    elif frames:
        df = frames[0]
    else:
        df = empty_frame(schema)
    df = df.sort_values("ts", kind="stable", ignore_index=True)
    return validate(df, schema)


def load_trades(start: date, end: date, symbol: str = "XBTUSD", *,
                out_dir: Path = DEFAULT_OUT_DIR, allow_missing: bool = False) -> pd.DataFrame:
    """`start`~`end`(포함) 정규화 체결. 결측 일이 있으면 기본 `MissingDaysError`."""
    return _load("trades", start, end, symbol, out_dir, allow_missing)


def load_bars(start: date, end: date, symbol: str = "XBTUSD", *,
              out_dir: Path = DEFAULT_OUT_DIR, allow_missing: bool = False) -> pd.DataFrame:
    """`start`~`end`(포함) 1분봉. 결측 일이 있으면 기본 `MissingDaysError`."""
    return _load("bars_1m", start, end, symbol, out_dir, allow_missing)


FILLS_DIRS = {"aoa": aoa.DEFAULT_OUT_DIR}


def _ymd_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def load_fills(start: date, end: date, source: str = "aoa", *, out_dir: Path | None = None) -> pd.DataFrame:
    """`start`~`end`(포함) 행동 체결. 있는 일 파일만 이어 붙인다(거래 없는 날 = 파일 없음, 오류 아님)."""
    if source not in FILLS_DIRS:
        raise ValueError(f"알 수 없는 source: {source!r} (허용 {sorted(FILLS_DIRS)})")
    _check_range(start, end)
    d = Path(out_dir) if out_dir is not None else FILLS_DIRS[source]
    mpath = aoa.manifest_path(d)
    if not mpath.is_file():
        raise FileNotFoundError(f"{mpath} 없음 — `python -m src.ingest.aoa` 로 정규화를 먼저 실행")
    days = sorted(json.loads(mpath.read_text(encoding="utf-8")).get("days", {}))
    if days and (start < _ymd_date(days[0]) or end > _ymd_date(days[-1])):
        log.warning("요청 구간 %s~%s 이 %s 일 범위 %s~%s 밖으로 나감", start, end, source, days[0], days[-1])
    frames = [pd.read_parquet(p, engine="pyarrow") for p in (aoa.fills_path(d, day) for day in _days(start, end))
              if p.is_file()]
    nonempty = [f for f in frames if len(f)]
    df = pd.concat(nonempty, ignore_index=True) if nonempty else empty_frame(FILLS)
    df = df.sort_values("ts", kind="stable", ignore_index=True)
    return validate(df, FILLS)
