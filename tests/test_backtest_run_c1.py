"""backtest.run_c1 — 합성 1분봉(실데이터 금지)에서 C1 판정 엔진 배선: 메이커만 선택·테이커는 같은 파라미터·
네 경우 곡선·군집 DSR·v1 정합 검사·캐시·OOS 거부."""

import json
import math

import pandas as pd
import pytest

from src.analysis.c1 import C1Params
from src.backtest import run_c1 as rc
from src.backtest.walkforward import Fold, OOS_START, SAMPLE_START
from tests.test_backtest_wfengine import FOLDS, LOOSE, PARAMS, SAMPLE, Loader

GRID = [C1Params(n=15, k=k, exit=x, stop_sigma=2.0) for k in (1.5, 2.0) for x in ("tp", "time")]
V1 = [p for p in PARAMS if p.risk_pct == 1.0]


@pytest.fixture(scope="module")
def rep():
    return rc.run_c1_walkforward(FOLDS, Loader(), grid=GRID, gate=LOOSE, sample=SAMPLE, v1_params=V1)


def test_selection_uses_maker_only_and_taker_same_params(rep):
    assert rep["n_runs"] == 8 and len(rep["folds"]) == 2
    for f in rep["folds"]:
        assert {t["strategy_id"] for t in f["train"]} == {"syn-c1-maker"} and len(f["train"]) == 4
        sel = f["selection"]
        assert sel is not None and sel["strategy_id"] == "syn-c1-maker"
        assert set(f["test"]) == {"maker", "maker_bybit", "maker_nofunding", "taker"}
        if f["test"]["taker"]["n_trades"]:
            assert f["test"]["taker"]["strategy_id"] == "syn-c1-taker"
            assert f["test"]["taker"]["param_id"] == sel["param_id"]
        for k in rc.C1_EXTRA_KEYS:
            assert k in sel


def test_stitched_and_verdict(rep):
    assert set(rep["stitched"]) == {"maker", "maker_bybit", "maker_nofunding", "taker"}
    n = sum(f["test"]["maker"]["n_trades"] for f in rep["folds"])
    assert rep["stitched"]["maker"]["n_trades"] == n
    assert rep["verdict"] in ("pass", "fail", "insufficient")
    assert set(rep["sensitivity_verdicts"]) == {"maker_bybit", "maker_nofunding", "taker"}
    assert rep["funding"] is False  # 펀딩 없이 호출 → 펀딩 열 없음


def test_dsr_cluster_wiring(rep):
    d, fr = rep["dsr"], rep["_frames"]
    assert rep["n_trials_raw"] == len(V1) + 2 * len(GRID) == fr["returns"].shape[1]
    assert len(rep["full_sample"]["runs"]) == 2 * len(GRID)
    assert 1 <= d["cluster"]["n_eff"] <= rep["n_trials_raw"]
    st = rep["stitched"]["maker"]
    expect = rc.deflated_sharpe(st["sr_daily"], d["cluster"]["n_eff"], d["cluster"]["var_sr"], st["n_days"],
                                st["skew_daily"], st["kurt_daily"])
    assert (math.isnan(expect) and math.isnan(d["cluster"]["maker"])) or d["cluster"]["maker"] == expect
    assert d["a_trial"]["n"] == d["b_joint"]["n"] == rep["n_trials_raw"]
    # 일 수익률 행 = 표본 일 수, 군집 라벨은 모든 run
    assert len(fr["returns"]) == (SAMPLE[1] - SAMPLE[0]).days and len(fr["labels"]) == rep["n_trials_raw"]


def test_v1_reference_mismatch_stops(rep):
    c = rep["dsr"]["v1_check"]
    ref = {"var_sr": c["var_sr"], "n_var_runs": c["n_runs"], "n_var_finite": c["n_finite"]}
    for bad in ({**ref, "var_sr": ref["var_sr"] * (1 + 1e-6)}, {**ref, "n_var_runs": ref["n_var_runs"] + 1},
                {**ref, "n_var_finite": ref["n_var_finite"] - 1}):
        with pytest.raises(ValueError, match="v1 대표 run"):
            rc.run_c1_walkforward(FOLDS, Loader(), grid=GRID, gate=LOOSE, sample=SAMPLE,
                                  v1_returns=rep["_frames"]["v1_returns"], v1_ref=bad)
    ok = rc.run_c1_walkforward(FOLDS, Loader(), grid=GRID, gate=LOOSE, sample=SAMPLE,
                               v1_returns=rep["_frames"]["v1_returns"], v1_ref=ref)
    assert ok["dsr"]["cluster"]["n_eff"] == rep["dsr"]["cluster"]["n_eff"]
    assert ok["stitched"] == rep["stitched"]


def test_outputs_and_v1_cache_roundtrip(rep, tmp_path):
    r = dict(rep)
    paths = rc.output_paths(tmp_path, True)
    v1_ret, v1_sr = rep["_frames"]["v1_returns"]
    rc.write_outputs(paths, r)
    obj = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert obj["verdict"] == rep["verdict"] and "_frames" not in obj
    assert "# C1 워크포워드 판정" in paths["md"].read_text(encoding="utf-8")
    ret2, sr2 = rc.read_v1_cache(paths["v1_returns"])
    pd.testing.assert_frame_equal(ret2, v1_ret, check_freq=False, check_names=False)
    pd.testing.assert_series_equal(sr2, v1_sr, check_names=False)
    assert rc.read_v1_cache(tmp_path / "none.parquet") is None


def test_parallel_equals_sequential(rep):
    par = rc.run_c1_walkforward(FOLDS, Loader(), grid=GRID, gate=LOOSE, sample=SAMPLE, v1_params=V1, jobs=2)
    assert par["stitched"] == rep["stitched"] and par["full_sample"] == rep["full_sample"]
    assert par["dsr"]["cluster"]["n_eff"] == rep["dsr"]["cluster"]["n_eff"]


def test_oos_folds_rejected_before_loading():
    loader = Loader()
    bad = [Fold(SAMPLE_START, OOS_START, OOS_START, OOS_START + pd.Timedelta(days=30))]
    with pytest.raises(ValueError):
        rc.run_c1_walkforward(bad, loader, grid=GRID)
    assert loader.calls == []


def test_cli_requires_v1_report(tmp_path):
    with pytest.raises(SystemExit) as e:
        rc.main(["--v1-report", str(tmp_path / "missing.json"), "--out", str(tmp_path)])
    assert e.value.code == 2


def test_daily_returns_match_summary_sr(rep):
    """군집 입력(일 수익률)과 V 입력(요약 sr_daily)이 같은 net 에서 나왔다."""
    fr = rep["_frames"]
    for col in fr["returns"].columns:
        sr = rc.daily_moments(fr["returns"][col].to_numpy())["sr_daily"]
        ref = fr["sr_daily"][col]
        assert (math.isnan(sr) and math.isnan(ref)) or abs(sr - ref) <= 1e-12


def test_max_k_exhausted_falls_back_to_joint(rep):
    r = rc.run_c1_walkforward(FOLDS, Loader(), grid=GRID, gate=LOOSE, sample=SAMPLE,
                              v1_returns=rep["_frames"]["v1_returns"], max_k_steps=(2,))
    if r["dsr"]["cluster"]["hit_max_k"]:
        assert r["dsr"]["judge"] == "b_joint"
    assert rep["dsr"]["judge"] in ("cluster", "b_joint")
