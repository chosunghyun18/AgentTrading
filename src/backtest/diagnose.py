"""Phase 3 진단 CLI: 워크포워드 폴드별 학습·검증 구간 **전 run** 요약 덤프(default 프로필, 펀딩 선택).

판정 경로(`run.py --walkforward`·`--oos-final`)가 아니다 — 선택·판정·선택 파일을 만들지 않고, 미달 원인 분석
(트리거별 성과, 학습-검증 Sharpe 괴리)용 표만 쓴다. `run.py` 의 `run_grid`·`_check_folds`·`_load_range` 를
그대로 재사용하므로 폴드별 학습 요약은 `run_walkforward` 내부 학습 그리드와 같다.
설계 근거(단일 기준): Obsidian `Projects/work/AgentTrading/design/phase3-backtest.md` "모듈".

    python -m src.backtest.diagnose --grid grid.json --jobs 4
    python -m src.backtest.diagnose --funding --funding-dir data/raw/normalized/bitmex/funding

| 출력 | 경로 |
|---|---|
| 폴드 × phase(train/test) × run 요약 | `<out>/diagnose/folds.parquet` (펀딩 켜면 `folds+funding.parquet`) |

- 폴드는 `make_folds()` 기본 표본 6폴드. 모든 구간이 [2018-03-01, 2022-01-01) 안이어야 하며, 벗어나면(OOS 포함)
  데이터를 읽기 전에 거부(종료코드 2). 실행 중 예외는 로그 후 종료코드 1, 산출물 미작성(`.tmp` → `os.replace`).
- `total_funding_xbt` 는 펀딩 끔이면 NaN(미반영 표시, 0.0 = 펀딩 0 과 구분). 결과는 `--jobs` 와 무관하게 같다.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pandas as pd

from src.analysis.run import load_grid
from src.analysis.synthetic import Params
from src.backtest import run as br
from src.backtest.walkforward import OOS_START, SAMPLE_START, Fold, make_folds, resolve_gate
from src.ingest import bitmex_funding, normalize

log = logging.getLogger(__name__)

DEFAULT_DIAG_SUBDIR = "diagnose"
PHASES = ("train", "test")
DIAGNOSE_COLUMNS = ("fold", "phase", "strategy_id", "param_id", "trigger", "risk_pct", "n_trades", "sharpe",
                    "sr_daily", "mdd", "total_gross_ret", "total_net_ret", "total_funding_xbt")
_DTYPES = {"fold": "int64", "phase": "string", "strategy_id": "string", "param_id": "string",
           "trigger": "string", "risk_pct": "float64", "n_trades": "int64", "sharpe": "float64",
           "sr_daily": "float64", "mdd": "float64", "total_gross_ret": "float64", "total_net_ret": "float64",
           "total_funding_xbt": "float64"}
_METRICS = ("sharpe", "sr_daily", "mdd", "total_gross_ret", "total_net_ret")


def _float(v) -> float:
    return math.nan if v is None else float(v)


def _rows(fold: int, phase: str, summaries: Sequence[Mapping], by_key: Mapping[tuple, Params],
          funded: bool) -> list[dict]:
    """`run_grid` 요약 목록 → 진단 행(`trigger`·`risk_pct` 는 (strategy_id, param_id) 로 Params 조인)."""
    rows = []
    for s in summaries:
        p = by_key[(s["strategy_id"], s["param_id"])]
        row = {"fold": fold, "phase": phase, "strategy_id": s["strategy_id"], "param_id": s["param_id"],
               "trigger": p.trigger, "risk_pct": float(p.risk_pct), "n_trades": int(s["n_trades"])}
        row.update({k: _float(s[k]) for k in _METRICS})
        row["total_funding_xbt"] = float(s["total_funding_xbt"]) if funded else math.nan
        rows.append(row)
    return rows


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(DIAGNOSE_COLUMNS)).astype(_DTYPES)
    order = df["phase"].map({ph: i for i, ph in enumerate(PHASES)})
    df = df.assign(_ph=order).sort_values(["fold", "_ph", "strategy_id", "param_id"], kind="stable")
    return df.drop(columns="_ph").reset_index(drop=True)


def diagnose_folds(folds: Sequence[Fold], load_bars: Callable, params_list: Sequence[Params],
                   gate: Mapping | None = None, sample=(SAMPLE_START, OOS_START), jobs: int = 1,
                   funding: pd.DataFrame | None = None) -> pd.DataFrame:
    """폴드마다 학습 구간 → 검증 구간 순으로 전 run `run_grid`(`default`) 를 돌려 `DIAGNOSE_COLUMNS` 프레임을 만든다.

    `load_bars(start, end)` 는 `run_walkforward` 와 같은 반열린 `[start, end)` 주입 로더다. 폴드 검사(`_check_folds`)는
    어떤 바도 읽기 전에 한다. 게이트는 요약의 `gate` 필드 계산에만 쓰이고 프레임에는 넣지 않는다.
    """
    g = resolve_gate(gate)
    br._check_folds(folds, sample)
    by_key = {(p.strategy_id, p.param_id): p for p in params_list}
    funded = funding is not None
    rows: list[dict] = []
    for i, f in enumerate(folds, 1):
        for phase, (start, end) in zip(PHASES, ((f.train_start, f.train_end), (f.test_start, f.test_end))):
            log.info("폴드 %d %s [%s, %s)", i, phase, br._fmt_day(start), br._fmt_day(end))
            bars = br._load_range(load_bars, start, end)
            summaries = br.run_grid(bars, params_list, "default", start, end, g, br._DiscardSink(), jobs, funding)
            del bars
            rows.extend(_rows(i, phase, summaries, by_key, funded))
    return _frame(rows)


def diagnose_path(out_dir: Path, funded: bool = False) -> Path:
    return out_dir / DEFAULT_DIAG_SUBDIR / f"{br._funding_label('folds', funded)}.parquet"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.backtest.diagnose",
        description="워크포워드 기본 표본 6폴드 × 학습·검증 구간 전 run 요약 → diagnose/folds[+funding].parquet"
                    " (Phase 3 진단, 판정 경로 아님)")
    p.add_argument("--symbol", default="XBTUSD")
    p.add_argument("--grid", type=Path, default=None,
                   help="축 → 값 목록 JSON (설계 그리드 부분집합, 생략 시 전체 2,268 run)")
    p.add_argument("--data-dir", type=Path, default=normalize.DEFAULT_OUT_DIR,
                   help=f"정규화 데이터 디렉터리 (기본 {normalize.DEFAULT_OUT_DIR})")
    p.add_argument("--out", type=Path, default=br.DEFAULT_OUT_DIR, help=f"출력 디렉터리 (기본 {br.DEFAULT_OUT_DIR})")
    p.add_argument("--jobs", type=int, default=1, metavar="N",
                   help="run 병렬 프로세스 수 (기본 1 = 순차). 결과는 N 과 무관하게 같다")
    p.add_argument("--funding", action="store_true",
                   help="펀딩 비용 반영(기본 끔). 실행 전 기본 표본 펀딩 결측 검사(결측 → 종료코드 1)")
    p.add_argument("--funding-dir", type=Path, default=None, metavar="PATH",
                   help=f"펀딩 parquet 디렉터리 (기본 {bitmex_funding.DEFAULT_FUNDING_DIR}, --funding 과만)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    a = parser.parse_args(argv)
    if a.jobs < 1:
        parser.error(f"--jobs 는 1 이상이어야 한다: {a.jobs}")
    if a.funding_dir is not None and not a.funding:
        parser.error("--funding-dir 는 --funding 과 함께만 쓴다")
    if a.funding_dir is None:
        a.funding_dir = bitmex_funding.DEFAULT_FUNDING_DIR
    try:
        _, params_list = load_grid(a.grid)
    except (OSError, ValueError) as e:  # json.JSONDecodeError 는 ValueError
        parser.error(f"--grid: {e}")
    folds = make_folds()
    try:  # 데이터를 읽기 전에 표본 가드(OOS·표본 밖 → 종료코드 2)
        br._check_folds(folds, (SAMPLE_START, OOS_START))
    except ValueError as e:
        parser.error(f"폴드: {e}")

    t0 = time.monotonic()
    path = diagnose_path(a.out, a.funding)
    funding = None
    try:
        if a.funding:  # 폴드 학습·검증 합집합 = 기본 표본 전체를 실행 전에 한 번 검사
            funding, _ = br._load_checked_funding(a.symbol, SAMPLE_START, OOS_START, a.funding_dir)
        df = diagnose_folds(folds, br.store_loader(a.symbol, a.data_dir), params_list, gate=resolve_gate(None),
                            jobs=a.jobs, funding=funding)
        normalize.write_parquet_atomic(df, path)
    except Exception:
        log.exception("진단 실패")
        return 1
    log.info("완료 (%.1fs): %s (%d행)", time.monotonic() - t0, path, len(df))
    return 0


if __name__ == "__main__":
    sys.exit(main())
