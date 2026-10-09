"""backtest.export_equity — 합성 1분봉 워크포워드 리포트로 검증 곡선 재구성이 판정 stitched 와 같은지 확인."""

import json

import numpy as np
import pandas as pd
import pytest

from src.backtest import export_equity as ee
from src.backtest import run as br
from tests.test_backtest_run_funding import funding_frame
from tests.test_backtest_wfengine import FOLDS, LOOSE, PARAMS, SAMPLE, SYM, Loader

AXES = {"trigger": ["h1", "h2"]}


def _report(funding=None):
    rep = br.run_walkforward(FOLDS, Loader(), PARAMS, gate=LOOSE, sample=SAMPLE, funding=funding)
    meta = None if funding is None else {"source": "test", "n_settlements": len(funding)}
    return br.build_walkforward_report(rep, SYM, AXES, meta)


@pytest.fixture(scope="module")
def plain():
    return _report()


@pytest.fixture(scope="module")
def funded():
    f = funding_frame("2020-03-01", "2020-03-08")
    return _report(f), f


@pytest.mark.parametrize("which", ["plain", "funded"])
def test_reconstruction_matches_stitched(which, plain, funded):
    report, funding = (plain, None) if which == "plain" else funded
    assert any(fr["selection"] for fr in report["folds"])
    eq, trades, summaries = ee.export(report, Loader(), funding)
    for name in br.PROFILE_NAMES:
        want = report["stitched"][name]
        assert summaries[name]["n_trades"] == want["n_trades"]
        for k in ("sharpe", "mdd", "total_net_ret"):
            assert abs(summaries[name][k] - want[k]) <= 1e-9
        curve = eq[eq["profile"] == name]
        # 마지막 날 자본 − 1 = 총 net 수익률, 일 수 = 평가 구간 일 수
        assert abs(curve["equity"].iloc[-1] - 1 - want["total_net_ret"]) <= 1e-9
        assert len(curve) == want["n_days"]
    assert list(eq.columns) == list(ee.EQUITY_COLUMNS)
    assert str(eq["date"].dt.tz) == "UTC"
    assert len(trades) == report["stitched"]["default"]["n_trades"]
    assert trades["wf_trade_id"].tolist() == list(range(len(trades)))
    assert set(trades["fold"]) <= {1, 2}
    assert ("funding_xbt" in trades.columns) == (funding is not None)


def test_mismatch_rejected(plain):
    bad = json.loads(json.dumps(plain))
    bad["stitched"]["default"]["sharpe"] += 1e-6
    with pytest.raises(ValueError, match="stitched"):
        ee.export(bad, Loader())


def test_no_selection_fold_is_cash(plain):
    rep = json.loads(json.dumps(plain))
    rep["folds"][0]["selection"] = None
    loader = Loader()
    nets = ee.recompute_test_nets(rep, loader)
    assert nets["default"][0] is None and nets["bybit"][0] is None
    assert loader.calls == [(FOLDS[1].test_start, FOLDS[1].test_end)]  # 선택 없음 폴드는 바를 읽지 않음


@pytest.mark.parametrize("key, value", [("sample", ["2018-03-01", "2022-01-02"]),
                                        ("test_end", "2022-01-02")])
def test_oos_rejected_before_loading(plain, key, value):
    rep = json.loads(json.dumps(plain))
    if key == "sample":
        rep["sample"] = value
    else:
        rep["sample"] = ["2018-03-01", "2022-01-01"]
        rep["folds"][-1]["test_end"] = value
    loader = Loader()
    with pytest.raises(ValueError):
        ee.recompute_test_nets(rep, loader)
    assert loader.calls == []


def test_stale_fee_rejected(plain):
    rep = json.loads(json.dumps(plain))
    rep["meta"]["fee"]["default"]["taker_fee"] = 0.001
    with pytest.raises(ValueError, match="수수료"):
        ee.check_report(rep)


def test_cli_writes_outputs(tmp_path, plain, monkeypatch):
    path = tmp_path / "walkforward" / "default.json"
    path.parent.mkdir()
    path.write_text(json.dumps(plain), encoding="utf-8")
    monkeypatch.setattr(br, "store_loader", lambda symbol, data_dir: Loader())
    assert ee.main(["--report", str(path)]) == 0
    out = ee.export_paths(path)
    assert out["equity"].name == "default.equity.parquet" and out["trades"].name == "default.trades.parquet"
    eq = pd.read_parquet(out["equity"])
    assert set(eq["profile"]) == set(br.PROFILE_NAMES)
    assert np.isfinite(eq["equity"]).all()


def test_cli_bad_report_exit_2(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        ee.main(["--report", str(path)])
    assert e.value.code == 2
