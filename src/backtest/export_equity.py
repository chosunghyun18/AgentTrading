"""워크포워드 검증 자본곡선 산출 CLI: 판정 리포트의 폴드별 선택으로 검증 구간을 다시 계산해 파일로 남긴다.

판정 경로(`run.py --walkforward`)가 아니다 — 판정·선택 파일을 만들지 않고 `run.py`·`walkforward.py` 를 바꾸지 않는다.
`run_walkforward` 검증 단계와 같은 함수(`generate_run` → `costs.apply_costs` → `run._net_and_summary` →
`stitch_test_roundtrips`)를 같은 순서로 부르므로, 재구성한 이어 붙인 곡선 요약은 리포트 `stitched` 와 같아야 한다.
다르면(리포트와 데이터·코드가 어긋남) 아무것도 쓰지 않고 종료코드 1.
설계 근거(단일 기준): Obsidian `Projects/work/AgentTrading/design/user-dashboard.md` "필요한 신규 산출물".

    python -m src.backtest.export_equity                       # walkforward/default+funding.json
    python -m src.backtest.export_equity --report data/out/backtest/walkforward/default.json

| 출력 | 경로 |
|---|---|
| 일별 자본곡선(default·bybit) | `walkforward/<name>.equity.parquet` — `date`·`profile`·`equity`·`daily_ret` |
| 이어 붙인 검증 라운드트립(default) | `walkforward/<name>.trades.parquet` — net 열(+펀딩 2열) + `fold`·`wf_trade_id` |

- 펀딩은 리포트 `meta.funding` 이 있을 때만 적용한다(옵션이 아니라 리포트를 따른다).
- 폴드 구간은 표본 가드(`check_sample_range`)를 거친다 — OOS 를 읽지 않는다. 리포트 형식 문제는 종료코드 2.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis.synthetic import RULESET_VERSION, Params, generate_run, param_grid
from src.backtest import costs
from src.backtest import run as br
from src.backtest.metrics import daily_returns, equity_curve, summarize_net_run
from src.backtest.walkforward import Fold, check_sample_range, resolve_gate, stitch_test_roundtrips
from src.ingest import bitmex_funding, normalize

log = logging.getLogger(__name__)

DEFAULT_REPORT = br.walkforward_paths(br.DEFAULT_OUT_DIR, funded=True)["json"]
EQUITY_COLUMNS = ("date", "profile", "equity", "daily_ret")
CHECK_KEYS = ("n_trades", "sharpe", "mdd", "total_net_ret")
TOL = 1e-9


def export_paths(report_path: Path) -> dict[str, Path]:
    stem = report_path.with_suffix("")
    return {"equity": stem.with_name(stem.name + ".equity.parquet"),
            "trades": stem.with_name(stem.name + ".trades.parquet")}


def report_folds(report: Mapping) -> list[Fold]:
    """리포트 `folds` 의 날짜 문자열 → `Fold`(UTC 자정). 표본 가드는 `_check_folds` 가 한다."""
    T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    return [Fold(T(f["train_start"]), T(f["train_end"]), T(f["test_start"]), T(f["test_end"]))
            for f in report["folds"]]


def check_report(report: Mapping) -> None:
    """재계산 전제(규칙 버전·수수료 프로필)가 현재 코드와 같은지. 다르면 `ValueError`."""
    meta = report.get("meta") or {}
    if meta.get("ruleset_version") != RULESET_VERSION:
        raise ValueError(f"규칙 버전 불일치: 리포트 {meta.get('ruleset_version')!r} ≠ 코드 {RULESET_VERSION!r}")
    for name in br.PROFILE_NAMES:
        if (meta.get("fee") or {}).get(name) != costs.PROFILES[name]:
            raise ValueError(f"수수료 프로필 {name!r} 이 리포트와 코드에서 다르다")
    if not report.get("folds"):
        raise ValueError("리포트에 폴드가 없다")


def _params_by_key() -> dict[tuple[str, str], Params]:
    return {(p.strategy_id, p.param_id): p for p in param_grid()}


def recompute_test_nets(report: Mapping, load_bars: Callable, funding: pd.DataFrame | None = None
                        ) -> dict[str, list[pd.DataFrame | None]]:
    """폴드별 검증 net(프로필별 목록, 선택 없음 = None). `run_walkforward` 검증 단계와 같은 호출 순서.

    펀딩 켬이면 34열(펀딩 2열 포함) 그대로 돌려준다 — 이어 붙이기 전에 `run._project_net` 으로 투영한다.
    """
    g = resolve_gate(report["gate"])
    folds = report_folds(report)
    br._check_folds(folds, check_sample_range(*report["sample"]))
    by_key = _params_by_key()
    nets: dict[str, list] = {name: [] for name in br.PROFILE_NAMES}
    for i, (f, fr) in enumerate(zip(folds, report["folds"]), 1):
        sel = fr["selection"]
        if sel is None:
            for name in br.PROFILE_NAMES:
                nets[name].append(None)
            continue
        try:
            p = by_key[(sel["strategy_id"], sel["param_id"])]
        except KeyError:
            raise ValueError(f"폴드 {i}: 설계 그리드에 없는 선택 {sel['strategy_id']} {sel['param_id']}") from None
        bars = br._load_range(load_bars, f.test_start, f.test_end)
        res = generate_run(bars, p)
        for name in br.PROFILE_NAMES:
            net, _ = br._net_and_summary(costs.apply_costs(res.roundtrips, name), f.test_start, f.test_end, g,
                                         funding, bars)
            nets[name].append(net)
        del res, bars
        log.info("폴드 %d 검증 재계산 [%s, %s): %s", i, f.test_start.date(), f.test_end.date(), p.param_id)
    return nets


def _stitch(fold_nets: Sequence[pd.DataFrame | None], funded: bool) -> pd.DataFrame:
    return stitch_test_roundtrips([None if n is None else (br._project_net(n) if funded else n) for n in fold_nets])


def _close(a, b) -> bool:
    """1e-9 이내. JSON 의 None(리포트 쓰기 때 NaN → None)은 NaN 으로 본다."""
    a, b = (float("nan") if x is None else float(x) for x in (a, b))
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return abs(a - b) <= TOL


def verify_stitched(report: Mapping, stitched: Mapping[str, pd.DataFrame]) -> dict[str, dict]:
    """재구성 곡선 요약 vs 리포트 `stitched`(`CHECK_KEYS`, 1e-9). 불일치면 `ValueError`, 일치면 요약 반환."""
    g = resolve_gate(report["gate"])
    folds = report_folds(report)
    start, end = folds[0].test_start, folds[-1].test_end
    out = {}
    for name in br.PROFILE_NAMES:
        got = summarize_net_run(stitched[name], start, end, g)
        want = report["stitched"][name]
        bad = [k for k in CHECK_KEYS if not _close(got[k], want.get(k))]
        if bad:
            raise ValueError(f"{name}: 재구성 요약이 리포트 stitched 와 다르다 "
                             + ", ".join(f"{k} {got[k]!r} ≠ {want.get(k)!r}" for k in bad))
        out[name] = got
    return out


def equity_frame(report: Mapping, stitched: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """프로필별 일별 자본곡선: `metrics.equity_curve` → `daily_returns`(UTC 자정) → 누적곱. `date` = 그날 자정."""
    folds = report_folds(report)
    start, end = folds[0].test_start, folds[-1].test_end
    parts = []
    for name in br.PROFILE_NAMES:
        daily = daily_returns(equity_curve(stitched[name], start=start), start, end)
        parts.append(pd.DataFrame({"date": daily.index, "profile": name,
                                   "equity": np.cumprod(1.0 + daily.to_numpy(dtype=float)),
                                   "daily_ret": daily.to_numpy(dtype=float)}))
    return pd.concat(parts, ignore_index=True)[list(EQUITY_COLUMNS)]


def trades_frame(fold_nets: Sequence[pd.DataFrame | None], stitched: pd.DataFrame) -> pd.DataFrame:
    """default 검증 라운드트립(원래 run 키 유지) + `fold`(1부터) + `wf_trade_id`(= 이어 붙인 곡선의 trade_id).

    `stitch_test_roundtrips` 와 같은 정렬(폴드 → entry_ts → trade_id)이라 행 순서가 이어 붙인 곡선과 같다.
    """
    parts = []
    for i, net in enumerate(fold_nets, 1):
        if net is None or len(net) == 0:
            continue
        p = net.copy()
        p["fold"] = np.int64(i)
        parts.append(p)
    if not parts:
        return pd.DataFrame(columns=[*stitched.columns, "fold", "wf_trade_id"])
    out = pd.concat(parts, ignore_index=True)
    out = out.sort_values(["fold", "entry_ts", "trade_id"], kind="mergesort").reset_index(drop=True)
    if len(out) != len(stitched) or not np.array_equal(out["net_ret"].to_numpy(), stitched["net_ret"].to_numpy()):
        raise ValueError("거래 목록과 이어 붙인 곡선의 순서·값이 다르다")
    out["wf_trade_id"] = stitched["trade_id"].to_numpy()
    return out


def export(report: Mapping, load_bars: Callable, funding: pd.DataFrame | None = None
           ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """(자본곡선, default 거래 목록, 검증된 재구성 요약). 쓰기는 호출자."""
    check_report(report)
    funded = funding is not None
    nets = recompute_test_nets(report, load_bars, funding)
    stitched = {name: _stitch(nets[name], funded) for name in br.PROFILE_NAMES}
    summaries = verify_stitched(report, stitched)
    return equity_frame(report, stitched), trades_frame(nets["default"], stitched["default"]), summaries


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.backtest.export_equity",
        description="워크포워드 리포트의 폴드별 선택으로 검증 구간 재계산 → <name>.equity.parquet·<name>.trades.parquet"
                    " (UI 용, 판정 경로 아님)")
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT, help=f"워크포워드 리포트 JSON (기본 {DEFAULT_REPORT})")
    p.add_argument("--data-dir", type=Path, default=normalize.DEFAULT_OUT_DIR,
                   help=f"정규화 데이터 디렉터리 (기본 {normalize.DEFAULT_OUT_DIR})")
    p.add_argument("--funding-dir", type=Path, default=bitmex_funding.DEFAULT_FUNDING_DIR,
                   help=f"펀딩 parquet 디렉터리 (기본 {bitmex_funding.DEFAULT_FUNDING_DIR}, 리포트에 펀딩이 있을 때만 읽음)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    a = parser.parse_args(argv)
    try:
        report = json.loads(a.report.read_text(encoding="utf-8"))
        check_report(report)
        folds = report_folds(report)
        br._check_folds(folds, check_sample_range(*report["sample"]))
    except (OSError, ValueError, KeyError, TypeError) as e:  # 데이터를 읽기 전 리포트 문제 → 2
        parser.error(f"--report: {e}")
    symbol = report["meta"].get("symbol", "XBTUSD")
    t0 = time.monotonic()
    paths = export_paths(a.report)
    try:
        funding = None
        if report["meta"].get("funding"):
            funding, _ = br._load_checked_funding(symbol, folds[0].test_start, folds[-1].test_end, a.funding_dir)
        eq, trades, summaries = export(report, br.store_loader(symbol, a.data_dir), funding)
        normalize.write_parquet_atomic(eq, paths["equity"])
        normalize.write_parquet_atomic(trades, paths["trades"])
    except Exception:
        log.exception("자본곡선 산출 실패")
        return 1
    for name, s in summaries.items():
        log.info("%s: 거래 %d · Sharpe %.4f · MDD %.4f — 리포트 stitched 와 일치", name, s["n_trades"], s["sharpe"],
                 s["mdd"])
    log.info("완료 (%.1fs): %s, %s", time.monotonic() - t0, paths["equity"], paths["trades"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
