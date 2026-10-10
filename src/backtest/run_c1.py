"""C1 워크포워드 판정 CLI (시도 2): 메이커 24 run 선택 → 검증 → 이어 붙인 곡선 → 군집 DSR 판정 + 민감도.

v1 판정 경로(`run.py --walkforward`)는 바꾸지 않는다 — 이 모듈은 `run.py` 헬퍼를 재사용하는 별도 진입점이고 산출물은
`<out>/c1/` 에만 쓴다(v1 산출물 바이트 불변). 선택 파일·OOS 접근 없음 — 통과해도 `--oos-final`·Phase 4 는 사람 판단.
설계 근거(단일 기준): Obsidian `Projects/work/AgentTrading/design/phase3-c1-meanrev-maker.md` "판정 절차" 1~8,
`design/phase3-next-hypotheses.md` 규칙 5(군집 DSR), 태스크 T-20261009-19 "계획".

    python -m src.backtest.run_c1 --jobs 4

| 단계 | 내용 |
|---|---|
| 폴드(6) | 학습 메이커 24 run default+펀딩 → `select_params` → 검증: 메이커 default+펀딩(판정)·bybit+펀딩·펀딩 끔, 테이커 default+펀딩 |
| 곡선 | 네 경우 각각 검증 net 이어 붙이기 → `summarize_net_run` |
| 표본 전체 | c1 48 run(메이커+테이커) + v1 대표 run 756(`risk_pct = 1`) default+펀딩 → 요약·일 수익률 |
| DSR | `cluster_trials`(804 run 일 수익률) → 실효 N·군집 V. 참고 (가) N 원시·V c1 48 · (나) N 원시·V 전체 |
| 판정 | `walkforward_verdict`(메이커 default+펀딩 곡선, 군집 DSR). 기준 완화 없음 |

- v1 대표 run 재계산 정합 검사: run 수·유한값 수가 v1 판정 리포트(`walkforward/default+funding.json`)의
  `dsr.n_var_runs`·`n_var_finite` 와 같고 `sr_daily` 표본 분산이 `dsr.var_sr` 와 상대 1e-9 안이 아니면 중단(종료코드 1).
- 군집에 넣는 일 수익률은 요약과 같은 net(펀딩 반영)에서 만들고, 그 수익률의 `sr_daily` 가 요약 값과 1e-12 안이어야 한다.
- 군집 상한 k 는 실행 전 고정 순서 `MAX_K_STEPS`(100 → 200 → 400, seed 0·n_init 10) — 상한에 닿지 않은 첫 결과를 쓰고,
  끝까지 닿으면 판정 값은 보수적 상한 (나)(N 원시·V 전체)로 한다(실효 N 과소 = 게이트 완화 방지).
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

import pandas as pd

from src.analysis.c1 import C1_GRID, FILLS, C1Params, generate_c1_run, strategy_id as c1_strategy_id
from src.analysis.synthetic import Params, generate_run, param_grid
from src.backtest import clustering, costs
from src.backtest import run as br
from src.backtest.metrics import daily_moments, daily_returns, equity_curve, summarize_net_run
from src.backtest.walkforward import (
    OOS_START,
    SAMPLE_START,
    check_sample_range,
    deflated_sharpe,
    expected_max_sr,
    make_folds,
    resolve_gate,
    select_params,
    sr_variance,
    stitch_test_roundtrips,
    walkforward_verdict,
)
from src.ingest import bitmex_funding, normalize
from src.shared.parallel import ordered_pool_map
from src.shared.schema import ROUNDTRIPS_NET, empty_frame

log = logging.getLogger(__name__)

C1_SUBDIR = "c1"
V1_REPORT = br.DEFAULT_OUT_DIR / "walkforward" / "default+funding.json"
V1_VAR_RTOL = 1e-9
SR_TOL = 1e-12
MAX_K_STEPS = (100, 200, 400)
# 검증 구간 네 경우: (이름, 체결 모드, 수수료 프로필, 펀딩 적용). 첫 줄이 판정.
VARIANTS = (("maker", "maker", "default", True), ("maker_bybit", "maker", "bybit", True),
            ("maker_nofunding", "maker", "default", False), ("taker", "taker", "default", True))
C1_EXTRA_KEYS = ("skipped_min_qty", "skipped_wide_stop", "unfilled_limit", "halted")


# run 1개 ---------------------------------------------------------------------------------------------

def compute_job(bars: pd.DataFrame, job: tuple, start, end, gate: Mapping, funding: pd.DataFrame | None,
                ) -> tuple[dict, pd.DataFrame | None, pd.Series | None]:
    """job = (kind, params, fill, profile, funded, keep_net, want_daily) → (요약, 32열 net 또는 None, 일 수익률 또는 None).

    kind `c1` 은 `generate_c1_run`(fill), `v1` 은 `generate_run`. 펀딩은 `funded` 이고 `funding` 이 있을 때만.
    """
    kind, params, fill, profile, funded, keep_net, want_daily = job
    if kind == "c1":
        res = generate_c1_run(bars, params, fill=fill)
        sid, extra = c1_strategy_id(fill), {k: getattr(res, k) for k in C1_EXTRA_KEYS}
    else:
        res = generate_run(bars, params)
        sid, extra = params.strategy_id, {}
    net = costs.apply_costs(res.roundtrips, profile)
    del res
    fund = funding if funded else None
    net, summary = br._net_and_summary(net, start, end, gate, fund, bars)
    summary = br._with_run_key(summary, sid, params.param_id)
    summary.update(extra)
    net = br._project_net(net) if fund is not None else net
    daily = None
    if want_daily:
        daily = daily_returns(equity_curve(net, start), start, end)
        sr, ref = daily_moments(daily.to_numpy())["sr_daily"], summary["sr_daily"]
        if not ((math.isnan(sr) and math.isnan(ref)) or abs(sr - ref) <= SR_TOL):
            raise ValueError(f"{sid} {params.param_id}: 일 수익률 sr_daily {sr!r} ≠ 요약 {ref!r}")
    return summary, (net if keep_net else None), daily


_WORKER: dict = {}


def _init_worker(bars, start, end, gate, funding) -> None:
    _WORKER.update(bars=bars, start=start, end=end, gate=gate, funding=funding)


def _worker(job: tuple):
    w = _WORKER
    return compute_job(w["bars"], job, w["start"], w["end"], w["gate"], w["funding"])


def run_jobs(bars, jobs_list: Sequence[tuple], start, end, gate, funding, jobs: int = 1) -> list:
    """job 목록을 입력 순서대로 계산(`jobs ≥ 2` 면 spawn 풀 — 결과는 순차와 같다)."""
    t0 = time.monotonic()
    if jobs >= 2 and len(jobs_list) >= 2:
        it = ordered_pool_map(_worker, jobs_list, jobs, initializer=_init_worker,
                              initargs=(bars, start, end, gate, funding))
    else:
        it = (compute_job(bars, j, start, end, gate, funding) for j in jobs_list)
    out = []
    for i, r in enumerate(it, 1):
        out.append(r)
        if i % br.PROGRESS_EVERY == 0 or i == len(jobs_list):
            log.info("run %d/%d (%.1fs)", i, len(jobs_list), time.monotonic() - t0)
    return out


# 판정 엔진 -------------------------------------------------------------------------------------------

def v1_representatives() -> list[Params]:
    """v1 DSR 대표 run(`risk_pct = 1`, 756개) — v1 `representative_var` 와 같은 모집단."""
    return [p for p in param_grid() if p.risk_pct == 1.0]


def _empty_summary(start, end, gate, funded: bool) -> dict:
    s = summarize_net_run(empty_frame(ROUNDTRIPS_NET), start, end, gate)
    if funded:
        s.update(br._empty_funding_totals())
    return s


def _dsr(st: Mapping, n: int, var: float) -> float:
    return deflated_sharpe(st["sr_daily"], n, var, st["n_days"], st["skew_daily"], st["kurt_daily"])


def run_c1_walkforward(folds, load_bars: Callable, grid: Sequence[C1Params] = C1_GRID, gate: Mapping | None = None,
                       sample=(SAMPLE_START, OOS_START), jobs: int = 1, funding: pd.DataFrame | None = None,
                       v1_params: Sequence[Params] | None = None, v1_returns: tuple | None = None,
                       v1_ref: Mapping | None = None, max_k_steps: Sequence[int] = MAX_K_STEPS) -> dict:
    """설계 "판정 절차" 1~6 + DSR(군집). 반환 dict 에 리포트와 저장용 프레임(`_frames`)을 담는다.

    `v1_returns` = (일 수익률 DataFrame, sr_daily Series) 캐시가 있으면 v1 대표 run 재계산을 건너뛴다.
    `v1_ref`(v1 리포트 `dsr`: var_sr·n_var_runs·n_var_finite)가 있으면 재계산 값이 같아야 한다(아니면 `ValueError`).
    """
    g = resolve_gate(gate)
    s, e = check_sample_range(*sample)
    br._check_folds(folds, (s, e))
    funded = funding is not None
    by_pid = {p.param_id: p for p in grid}
    v1_params = list(v1_representatives() if v1_params is None else v1_params)

    fold_reports, test_nets = [], {name: [] for name, *_ in VARIANTS}
    for i, f in enumerate(folds, 1):
        bars = br._load_range(load_bars, f.train_start, f.train_end)
        train_jobs = [("c1", p, "maker", "default", True, False, False) for p in grid]
        train = [r[0] for r in run_jobs(bars, train_jobs, f.train_start, f.train_end, g, funding, jobs)]
        del bars
        sel = select_params(train, g)  # 메이커 run 만 — 테이커는 선택에 쓰지 않는다(판정 절차 6)
        test, selection = {}, None
        if sel is None:
            log.info("폴드 %d: 선택 없음 → 현금 보유", i)
            for name, _, _, fv in VARIANTS:
                test[name] = _empty_summary(f.test_start, f.test_end, g, funded and fv)
                test_nets[name].append(None)
        else:
            p = by_pid[sel["param_id"]]
            selection = {"strategy_id": sel["strategy_id"], "param_id": p.param_id, "train_sharpe": sel["sharpe"],
                         "train_n_trades": sel["n_trades"], "train_mdd": sel["mdd"],
                         **{k: sel[k] for k in C1_EXTRA_KEYS}}
            bars = br._load_range(load_bars, f.test_start, f.test_end)
            vjobs = [("c1", p, fill, prof, fv, True, False) for _, fill, prof, fv in VARIANTS]
            for (name, *_), (summary, net, _) in zip(
                    VARIANTS, run_jobs(bars, vjobs, f.test_start, f.test_end, g, funding, 1)):
                test[name] = summary
                test_nets[name].append(net)
            del bars
        fold_reports.append({"train_start": br._fmt_day(f.train_start), "train_end": br._fmt_day(f.train_end),
                             "test_start": br._fmt_day(f.test_start), "test_end": br._fmt_day(f.test_end),
                             "n_candidates": sum(1 for t in train if t["n_trades"] >= g["min_trades"]
                                                 and math.isfinite(t["sharpe"]) and t["mdd"] <= g["max_drawdown"]),
                             "selection": selection, "test": test, "train": train})

    eval_start, eval_end = folds[0].test_start, folds[-1].test_end
    stitched = {}
    for name, _, _, fv in VARIANTS:
        st = br._with_run_key(summarize_net_run(stitch_test_roundtrips(test_nets[name]), eval_start, eval_end, g),
                              "walkforward", f"stitched:{name}")
        if funded and fv:
            st.update(n_funding=sum(fr["test"][name].get("n_funding", 0) for fr in fold_reports),
                      total_funding_xbt=sum(fr["test"][name].get("total_funding_xbt", 0.0) for fr in fold_reports))
        stitched[name] = st
    del test_nets

    # 표본 전체: c1 48 run(+ v1 대표 run 캐시 없으면 756) — 요약·일 수익률
    bars = br._load_range(load_bars, s, e)
    c1_jobs = [("c1", p, fill, "default", True, False, True) for fill in FILLS for p in grid]
    c1_full = run_jobs(bars, c1_jobs, s, e, g, funding, jobs)
    if v1_returns is None:
        v1_jobs = [("v1", p, None, "default", True, False, True) for p in v1_params]
        v1_full = run_jobs(bars, v1_jobs, s, e, g, funding, jobs)
        v1_ret = pd.DataFrame({_key(r[0]): r[2] for r in v1_full})
        v1_sr = pd.Series({_key(r[0]): r[0]["sr_daily"] for r in v1_full}, dtype=float)
    else:
        v1_ret, v1_sr = v1_returns
    del bars

    v1_var = sr_variance({"sr_daily": x} for x in v1_sr)
    v1_check = {"n_runs": len(v1_sr), "n_finite": int(v1_sr.notna().sum()), "var_sr": v1_var}
    if v1_ref is not None:
        ref_var = float(v1_ref["var_sr"])
        rel = abs(v1_var - ref_var) / abs(ref_var) if math.isfinite(v1_var) and ref_var else math.inf
        v1_check.update(ref_var_sr=ref_var, ref_n_runs=v1_ref.get("n_var_runs"),
                        ref_n_finite=v1_ref.get("n_var_finite"), rel_diff=rel)
        if (not rel <= V1_VAR_RTOL or v1_check["n_runs"] != v1_ref.get("n_var_runs")
                or v1_check["n_finite"] != v1_ref.get("n_var_finite")):
            raise ValueError(f"v1 대표 run 재계산이 v1 리포트와 다르다 — 중단: {v1_check}")

    c1_sum = [r[0] for r in c1_full]
    c1_ret = pd.DataFrame({_key(r[0]): r[2] for r in c1_full})
    c1_sr = pd.Series({_key(r[0]): r[0]["sr_daily"] for r in c1_full}, dtype=float)
    returns = pd.concat([v1_ret, c1_ret], axis=1)
    sr_all = pd.concat([v1_sr, c1_sr])
    n_raw = returns.shape[1]

    cl, used_max_k = _cluster(returns, sr_all, max_k_steps)
    dsr = {"cluster": {"n_eff": cl.effective_trials, "var_sr": cl.cluster_var,
                       "sr0": expected_max_sr(cl.effective_trials, cl.cluster_var), "max_k": used_max_k,
                       "max_k_steps": list(max_k_steps), "hit_max_k": cl.hit_max_k,
                       "silhouette": cl.silhouette_score, "n_input": cl.n_input,
                       "representatives": list(cl.representatives)},
           "a_trial": {"n": n_raw, "var_sr": sr_variance({"sr_daily": x} for x in c1_sr)},
           "b_joint": {"n": n_raw, "var_sr": sr_variance({"sr_daily": x} for x in sr_all)},
           "v1_check": v1_check}
    for ref in ("a_trial", "b_joint"):
        dsr[ref]["sr0"] = expected_max_sr(n_raw, dsr[ref]["var_sr"])
    for name in stitched:
        dsr["cluster"][name] = _dsr(stitched[name], cl.effective_trials, cl.cluster_var)
        for ref in ("a_trial", "b_joint"):
            dsr[ref][name] = _dsr(stitched[name], n_raw, dsr[ref]["var_sr"])
    judge = "b_joint" if cl.hit_max_k else "cluster"  # 상한에 끝까지 닿으면 보수적 상한 (나)
    dsr["judge"] = judge
    verdicts = {name: walkforward_verdict(stitched[name], dsr[judge][name], g) for name in stitched}

    return {
        "ruleset": "c1", "gate": g, "sample": [br._fmt_day(s), br._fmt_day(e)], "n_runs": len(grid) * len(FILLS),
        "n_trials_raw": n_raw, "funding": funded, "folds": fold_reports, "stitched": stitched, "dsr": dsr,
        "verdict": verdicts["maker"], "sensitivity_verdicts": {k: v for k, v in verdicts.items() if k != "maker"},
        "full_sample": {"runs": c1_sum},
        "_frames": {"returns": returns, "sr_daily": sr_all, "labels": cl.labels, "v1_returns": (v1_ret, v1_sr)},
    }


def _key(summary: Mapping) -> str:
    return f"{summary['strategy_id']}|{summary['param_id']}"


def _cluster(returns: pd.DataFrame, sr: pd.Series, steps: Sequence[int]):
    """실행 전 고정한 상한 순서로 `cluster_trials`(seed 0·n_init 10) — 상한에 닿지 않은 첫 결과, 끝까지 닿으면 마지막."""
    n = returns.shape[1]
    for i, k in enumerate(steps):
        used = min(int(k), n - 1)
        cl = clustering.cluster_trials(returns, sr, max_k=used, n_init=10, seed=0)
        if not cl.hit_max_k or used >= n - 1:
            return cl, used
        if i + 1 < len(steps):
            log.warning("군집 최적 k 가 상한 %d 에 닿음 → 다음 상한 %d", used, steps[i + 1])
    log.warning("군집 상한 %s 모두 닿음 → 판정 값은 보수적 상한 (나)", list(steps))
    return cl, used


# 출력 -------------------------------------------------------------------------------------------------

def output_paths(out_dir: Path, funded: bool = True) -> dict[str, Path]:
    d = Path(out_dir) / C1_SUBDIR
    lab = br._funding_label
    return {"json": d / "walkforward" / f"{lab('c1', funded)}.json", "md": d / "walkforward" / f"{lab('c1', funded)}.md",
            "returns": d / f"{lab('trials_returns', funded)}.parquet",
            "v1_returns": d / f"{lab('v1_returns', funded)}.parquet",
            "clusters": d / f"{lab('clusters', funded)}.json"}


def _num(v, fmt=".4f") -> str:
    return "–" if v is None or (isinstance(v, float) and not math.isfinite(v)) else format(v, fmt)


def render_markdown(rep: Mapping) -> str:
    g, d = rep["gate"], rep["dsr"]
    lines = [f"# C1 워크포워드 판정 (시도 2) — {rep['verdict']}", "",
             f"- 표본 {rep['sample'][0]} ~ {rep['sample'][1]} · run {rep['n_runs']}(메이커 24 판정 + 테이커 24 민감도) · "
             f"펀딩 {'반영' if rep['funding'] else '미반영'}",
             f"- 게이트: 거래 ≥ {g['min_trades']} · Sharpe ≥ {g['min_sharpe']} · MDD ≤ {g['max_drawdown']} · "
             f"DSR ≥ {g['min_dsr']} (군집 방식)",
             f"- 민감도 판정(참고): " + ", ".join(f"{k} {v}" for k, v in rep["sensitivity_verdicts"].items()), "",
             "## 폴드", "",
             "| 폴드 | 학습 | 검증 | 후보 | 선택 | 학습 Sharpe | 검증 거래 | 검증 Sharpe | 검증 MDD | 검증 net | 테이커 net |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, f in enumerate(rep["folds"], 1):
        sel, t = f["selection"], f["test"]
        lines.append(f"| {i} | {f['train_start']}~{f['train_end']} | {f['test_start']}~{f['test_end']} | "
                     f"{f['n_candidates']} | {sel['param_id'] if sel else '선택 없음'} | "
                     f"{_num(sel['train_sharpe'], '.3f') if sel else '–'} | {t['maker']['n_trades']} | "
                     f"{_num(t['maker']['sharpe'], '.3f')} | {_num(t['maker']['mdd'], '.3f')} | "
                     f"{_num(t['maker']['total_net_ret'], '+.2%')} | {_num(t['taker']['total_net_ret'], '+.2%')} |")
    lines += ["", "## 이어 붙인 검증 곡선", "",
              "| 경우 | 거래 | Sharpe | MDD | 총 net | 일 수 | 게이트 | DSR 군집 | DSR (가) | DSR (나) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for name, st in rep["stitched"].items():
        lines.append(f"| {name} | {st['n_trades']} | {_num(st['sharpe'], '.4f')} | {_num(st['mdd'], '.4f')} | "
                     f"{_num(st['total_net_ret'], '+.2%')} | {st['n_days']} | {st['gate']} | "
                     f"{_num(d['cluster'][name])} | {_num(d['a_trial'][name])} | {_num(d['b_joint'][name])} |")
    c = d["cluster"]
    lines += ["", "## DSR", "",
              f"- 판정 값 출처: **{'군집' if d['judge'] == 'cluster' else '(나) 합동 — 군집 상한 모두 닿음'}**. "
              f"군집 SR0 {_num(c['sr0'])} vs (나) {_num(d['b_joint']['sr0'])} — 군집이 SR0 를 얼마나 낮췄는지 비교용",
              f"- 군집(판정): 실효 N {c['n_eff']} · V {_num(c['var_sr'], '.6f')} · SR0(일) {_num(c['sr0'])} · "
              f"입력 run {c['n_input']} · 상한 k {c['max_k']}{' (상한 닿음)' if c['hit_max_k'] else ''} · "
              f"실루엣 품질 {_num(c['silhouette'], '.3f')}",
              f"- (가) 시도 V: N {d['a_trial']['n']} · V {_num(d['a_trial']['var_sr'], '.6f')} · SR0 {_num(d['a_trial']['sr0'])}",
              f"- (나) 합동 V: N {d['b_joint']['n']} · V {_num(d['b_joint']['var_sr'], '.6f')} · SR0 {_num(d['b_joint']['sr0'])}",
              f"- v1 대표 run 재계산 V {_num(d['v1_check']['var_sr'], '.9f')} (리포트 {_num(d['v1_check'].get('ref_var_sr'), '.9f')}, "
              f"run {d['v1_check']['n_runs']} · 유한 {d['v1_check']['n_finite']})",
              "", "## 표본 전체 48 run", "",
              "| strategy_id | param_id | 거래 | Sharpe | MDD | 총 net | 넓은 손절 건너뜀 | 지정가 미체결 | 소진 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in rep["full_sample"]["runs"]:
        lines.append(f"| {r['strategy_id']} | {r['param_id']} | {r['n_trades']} | {_num(r['sharpe'], '.3f')} | "
                     f"{_num(r['mdd'], '.3f')} | {_num(r['total_net_ret'], '+.2%')} | {r['skipped_wide_stop']} | "
                     f"{r['unfilled_limit']} | {r['halted']} |")
    lines += ["", "OOS(2022~) 미접근. 통과해도 `--oos-final`·Phase 4 착수는 사람 판단."]
    return "\n".join(lines) + "\n"


def write_outputs(paths: Mapping[str, Path], rep: dict) -> None:
    frames = rep.pop("_frames")
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
    normalize.write_parquet_atomic(frames["returns"].reset_index(names="date"), paths["returns"])
    v1_ret, v1_sr = frames["v1_returns"]
    normalize.write_parquet_atomic(_with_sr_row(v1_ret, v1_sr), paths["v1_returns"])
    clusters = {"labels": {str(k): int(v) for k, v in frames["labels"].items()},
                "representatives": rep["dsr"]["cluster"]["representatives"]}
    _atomic_text(paths["clusters"], json.dumps(clusters, ensure_ascii=False, indent=1))
    _atomic_text(paths["json"], br._json_text(br.to_jsonable(rep)))
    _atomic_text(paths["md"], render_markdown(rep))


def _with_sr_row(ret: pd.DataFrame, sr: pd.Series) -> pd.DataFrame:
    """v1 일 수익률 캐시: 첫 행 date=NaT 에 sr_daily 를 담는다(같은 파일 하나로 재사용)."""
    head = pd.DataFrame([sr.reindex(ret.columns).to_numpy()], columns=ret.columns)
    head.insert(0, "date", pd.NaT)
    body = ret.reset_index(names="date")
    body["date"] = body["date"].dt.tz_convert("UTC")
    head["date"] = pd.Series([pd.NaT], dtype=body["date"].dtype)
    return pd.concat([head, body], ignore_index=True)


def read_v1_cache(path: Path) -> tuple[pd.DataFrame, pd.Series] | None:
    if not path.is_file():
        return None
    df = pd.read_parquet(path)
    sr = df.iloc[0].drop("date").astype(float)
    ret = df.iloc[1:].set_index("date")
    ret.index = pd.DatetimeIndex(ret.index).tz_convert("UTC")
    ret.index.name = None
    return ret.astype(float), sr


def _atomic_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# CLI -------------------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.backtest.run_c1",
                                description="C1 워크포워드 판정(시도 2): 메이커 24 run 선택·테이커 민감도·군집 DSR → <out>/c1/")
    p.add_argument("--symbol", default="XBTUSD")
    p.add_argument("--data-dir", type=Path, default=normalize.DEFAULT_OUT_DIR)
    p.add_argument("--funding-dir", type=Path, default=bitmex_funding.DEFAULT_FUNDING_DIR)
    p.add_argument("--out", type=Path, default=br.DEFAULT_OUT_DIR)
    p.add_argument("--v1-report", type=Path, default=V1_REPORT,
                   help="v1 판정 리포트(대표 run V 정합 검사용, 기본 walkforward/default+funding.json)")
    p.add_argument("--jobs", type=int, default=1, metavar="N")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    a = parser.parse_args(argv)
    if a.jobs < 1:
        parser.error(f"--jobs 는 1 이상이어야 한다: {a.jobs}")
    folds = make_folds()
    try:
        br._check_folds(folds, (SAMPLE_START, OOS_START))
    except ValueError as e:
        parser.error(f"폴드: {e}")
    if not a.v1_report.is_file():
        parser.error(f"--v1-report 없음: {a.v1_report}")
    v1_ref = json.loads(a.v1_report.read_text(encoding="utf-8"))["dsr"]
    paths = output_paths(a.out, True)
    t0 = time.monotonic()
    try:
        funding, fmeta = br._load_checked_funding(a.symbol, SAMPLE_START, OOS_START, a.funding_dir)
        cache = read_v1_cache(paths["v1_returns"])
        if cache is not None:
            log.info("v1 대표 run 일 수익률 캐시 사용: %s", paths["v1_returns"])
        rep = run_c1_walkforward(folds, br.store_loader(a.symbol, a.data_dir), jobs=a.jobs, funding=funding,
                                 v1_returns=cache, v1_ref=v1_ref)
        rep["meta"] = {"symbol": a.symbol, "funding": fmeta, "ruleset_version": "c1", "v1_report": str(a.v1_report),
                       "grid": {"n": [60, 240], "k": [2.0, 2.5], "exit": ["mean", "tp", "time"], "stop_sigma": [1, 2]},
                       "elapsed_s": round(time.monotonic() - t0, 1)}
        write_outputs(paths, rep)
    except Exception:
        log.exception("C1 판정 실패")
        return 1
    log.info("완료 (%.1fs): 판정 %s → %s", time.monotonic() - t0, rep["verdict"], paths["json"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
