"""aoa(워뇨띠) 공개 BitMEX 원본 zip → 정규화 행동 체결 `fills`(source=aoa) 일별 parquet.

입력: 사람이 받아 둔 `data/raw/aoa_public_2021-12-31_with_letter.zip` (이름 변경·재배포 금지, 압축 해제 불필요).
출력: `data/raw/normalized/fills/aoa/YYYYMMDD.parquet`(UTC 일별, 전 심볼) + `_manifest.json`.
설계 근거(단일 기준): Obsidian `Projects/work/AgentTrading/design/phase1-ingest-schema.md` "행동 체결 스키마"
"aoa 원본 → 정규화 매핑", 진위 확인 `research/aoa-raw-verification.md`(T-20261002-07).

    python -m src.ingest.aoa                       # 기본 zip → 기본 출력
    python -m src.ingest.aoa --zip PATH --out DIR

| 원본 | fills | 변환 |
|---|---|---|
| transacttime | ts | UTC μs → datetime64[ns, UTC] |
| symbol · lastqty · lastpx | symbol · qty · price | 그대로(int64 · float64) |
| side | side | Buy/Sell → buy/sell |
| execcomm | fee | ÷ 1e8 → XBT, 부호 그대로(+ 지불, − 리베이트) |
| settlcurrency | fee_currency | XBt → XBT (그 밖 값이면 ValueError) |
| execid | source_id | 그대로(전 행 유일) |
| orderid · ordtype · trdmatchid | order_id · ord_type · trd_match_id | 빈 문자열 → null |
| lastliquidityind | liquidity | AddedLiquidity → maker, RemovedLiquidity → taker |

- `exectype == Trade` 행만 `fills` 로 쓴다. Funding·Settlement 는 범위 밖(건수만 로그). `leverage`·`strategy_id` 는 null.
- 모든 열을 문자열로 읽고, `lastqty`·`execcomm` 은 정수 문자열이 아니면(소수·빈 값) ValueError — float 경유 절단 없음.
  `side`·`lastliquidityind`·`settlcurrency` 가 예상 밖 값이어도 ValueError.
- 일 분할은 UTC `transacttime` 날짜 기준. 일 파일 안은 `ts` stable 정렬(같은 시각은 파일명 오름차순·행 순서 유지).
- `execid` 유일성은 일 분할 전 전체 프레임 `validate_fills` 로 검사한다(일 파일 사이 중복도 잡힘).
- 건너뛰기 = manifest 의 zip sha256·`LOADER_VERSION`·스키마 지문(`FILLS` 열·dtype) 일치 ∧ manifest 의 일 파일이
  모두 있고 parquet 행 수가 manifest 와 같음. 아니면 전체를 다시 쓰고(원자적 `*.tmp` → rename) → manifest 를
  원자적으로 쓴 뒤 → 새 결과에 없는 낡은 일 파일을 지운다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import zipfile
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from src.ingest.normalize import _replace_atomic, write_parquet_atomic
from src.shared.schema import FILLS, validate_fills

log = logging.getLogger(__name__)

DEFAULT_ZIP = Path("data/raw/aoa_public_2021-12-31_with_letter.zip")
DEFAULT_OUT_DIR = Path("data/raw/normalized/fills/aoa")
LOADER_VERSION = 1
EXEC_PREFIX = "aoa-execution-"
COLUMNS = ("execid", "orderid", "symbol", "side", "lastqty", "lastpx", "lastliquidityind", "settlcurrency",
           "exectype", "ordtype", "execcomm", "trdmatchid", "transacttime")
LIQUIDITY = {"AddedLiquidity": "maker", "RemovedLiquidity": "taker"}
SIDE = {"Buy": "buy", "Sell": "sell"}


def schema_fingerprint() -> str:
    """`FILLS` 열 이름·dtype 지문 — 스키마가 바뀌면 manifest 가 무효가 된다."""
    spec = [(c.name, str(c.dtype), c.nullable) for c in FILLS.columns]
    return hashlib.sha256(json.dumps(spec).encode()).hexdigest()[:16]


def _int_col(s: pd.Series, name: str) -> pd.Series:
    bad = ~s.str.fullmatch(r"-?\d+")
    if bad.any():
        raise ValueError(f"{name} 에 정수가 아닌 값 {int(bad.sum())}개(예: {s[bad].iloc[0]!r})")
    return s.astype("int64")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_executions(zip_path: Path) -> pd.DataFrame:
    """zip 안 `aoa-execution-*.csv` 전부(파일명 순)를 문자열 열로 읽어 이어 붙인다. 필요한 열만."""
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(n for n in zf.namelist() if Path(n).name.startswith(EXEC_PREFIX) and n.endswith(".csv"))
        if not names:
            raise ValueError(f"{zip_path}: {EXEC_PREFIX}*.csv 가 없다")
        frames = []
        for n in names:
            with zf.open(n) as f:
                frames.append(pd.read_csv(f, encoding="utf-8-sig", dtype=str, keep_default_na=False,
                                          usecols=list(COLUMNS)))
            log.info("%s %d행", n, len(frames[-1]))
    return pd.concat(frames, ignore_index=True)


def _nullable(s: pd.Series) -> pd.Series:
    return s.replace("", pd.NA).astype("string")


def to_fills(raw: pd.DataFrame) -> pd.DataFrame:
    """원본 체결(문자열 열) → `FILLS`(source=aoa). Trade 행만, `ts` stable 정렬."""
    counts = raw["exectype"].value_counts().to_dict()
    log.info("exectype 분포 %s — Trade 만 fills 로", counts)
    t = raw[raw["exectype"] == "Trade"]
    bad_cur = set(t["settlcurrency"]) - {"XBt"}
    if bad_cur:
        raise ValueError(f"settlcurrency 가 XBt 가 아닌 Trade 행: {sorted(bad_cur)}")
    for col, mapping in (("side", SIDE), ("lastliquidityind", LIQUIDITY)):
        bad = set(t[col]) - set(mapping)
        if bad:
            raise ValueError(f"알 수 없는 {col} 값: {sorted(bad)}")
    n = len(t)
    out = pd.DataFrame({
        "ts": pd.to_datetime(t["transacttime"], utc=True, format="mixed").dt.as_unit("ns"),
        "symbol": t["symbol"].astype("string"),
        "side": t["side"].map(SIDE).astype("string"),
        "qty": _int_col(t["lastqty"], "lastqty"),
        "price": t["lastpx"].astype("float64"),
        "leverage": np.full(n, np.nan),
        "fee": _int_col(t["execcomm"], "execcomm") / 1e8,
        "fee_currency": pd.array(["XBT"] * n, dtype="string"),
        "source": pd.array(["aoa"] * n, dtype="string"),
        "source_id": t["execid"].astype("string"),
        "strategy_id": pd.array([pd.NA] * n, dtype="string"),
        "order_id": _nullable(t["orderid"]),
        "liquidity": t["lastliquidityind"].map(LIQUIDITY).astype("string"),
        "ord_type": _nullable(t["ordtype"]),
        "trd_match_id": _nullable(t["trdmatchid"]),
    })
    out = out.sort_values("ts", kind="stable", ignore_index=True)
    return validate_fills(out.astype(FILLS.dtypes))


def fills_path(out_dir: Path, day) -> Path:
    return Path(out_dir) / f"{pd.Timestamp(day).strftime('%Y%m%d')}.parquet"


def manifest_path(out_dir: Path) -> Path:
    return Path(out_dir) / "_manifest.json"


def _current(manifest: dict, sha: str, out_dir: Path) -> bool:
    if (manifest.get("zip_sha256") != sha or manifest.get("version") != LOADER_VERSION
            or manifest.get("schema") != schema_fingerprint()):
        return False
    for d, rows in manifest.get("days", {}).items():
        p = fills_path(out_dir, d)
        if not p.is_file() or pq.ParquetFile(p).metadata.num_rows != rows:
            return False
    return True


def run(zip_path: Path = DEFAULT_ZIP, out_dir: Path = DEFAULT_OUT_DIR, force: bool = False) -> dict:
    """zip → 일별 fills. 반환 = manifest dict(`skipped` 키 추가). 최신이면 아무것도 쓰지 않는다."""
    zip_path, out_dir = Path(zip_path), Path(out_dir)
    sha = sha256_of(zip_path)
    mpath = manifest_path(out_dir)
    old = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {}
    if not force and _current(old, sha, out_dir):
        log.info("최신(sha %s…) — 건너뜀", sha[:12])
        return {**old, "skipped": True}

    fills = to_fills(read_executions(zip_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    day_key = fills["ts"].dt.strftime("%Y%m%d")
    days = {}
    for d, g in fills.groupby(day_key, sort=True):
        write_parquet_atomic(g.reset_index(drop=True), fills_path(out_dir, d))
        days[d] = len(g)
    manifest = {"zip": zip_path.name, "zip_sha256": sha, "version": LOADER_VERSION,
                "schema": schema_fingerprint(), "rows": int(len(fills)), "days": days}
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    _replace_atomic(lambda tmp: tmp.write_text(text, encoding="utf-8"), mpath)
    for p in out_dir.glob("[0-9]" * 8 + ".parquet"):  # 새 결과에 없는 낡은 일 파일
        if p.stem not in days:
            log.warning("낡은 일 파일 삭제 %s", p)
            p.unlink()
    log.info("fills %d행 · %d일 → %s", len(fills), len(days), out_dir)
    return {**manifest, "skipped": False}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.ingest.aoa",
                                description="aoa 공개 원본 zip → 정규화 fills(source=aoa) 일별 parquet")
    p.add_argument("--zip", type=Path, default=DEFAULT_ZIP, help=f"원본 zip (기본 {DEFAULT_ZIP})")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help=f"출력 디렉터리 (기본 {DEFAULT_OUT_DIR})")
    p.add_argument("--force", action="store_true", help="manifest 가 최신이어도 다시 쓴다")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    a = build_parser().parse_args(argv)
    if not a.zip.is_file():
        log.error("zip 없음: %s", a.zip)
        return 2
    try:
        run(a.zip, a.out, a.force)
    except Exception:
        log.exception("aoa fills 정규화 실패")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
