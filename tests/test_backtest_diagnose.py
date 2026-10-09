"""backtest.diagnose — 합성 폴드·합성 1분봉으로 폴드별 전 run 요약 덤프를 `run_walkforward` 와 대조."""

import json
import math

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from src.analysis.run import expand_grid
from src.backtest import diagnose as diag
from src.backtest import run as br
from src.backtest.walkforward import Fold, select_params
from tests.test_backtest_run_funding import write_fund
from tests.test_backtest_wfcli import FakeLoadBars, FoldsSpy, _loose_gate
from tests.test_backtest_wfengine import FOLDS, GRID, LOOSE, SAMPLE, Loader

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
_, PARAMS = expand_grid(GRID)
BY_KEY = {(p.strategy_id, p.param_id): p for p in PARAMS}


@pytest.fixture(scope="module")
def frame():
    return diag.diagnose_folds(FOLDS, Loader(), PARAMS, gate=LOOSE, sample=SAMPLE)


@pytest.fixture(scope="module")
def walkforward():
    """`run_walkforward` 실행 중 `run_grid` 호출(구간, 요약)을 기록한다 — 앞 2회가 폴드 학습 그리드."""
    calls = []
    orig = br.run_grid
    mp = pytest.MonkeyPatch()

    def spy(bars, params_list, fee_profile, start, end, *a, **k):
        res = orig(bars, params_list, fee_profile, start, end, *a, **k)
        calls.append(((start, end), [dict(s) for s in res]))
        return res

    mp.setattr(br, "run_grid", spy)
    try:
        rep = br.run_walkforward(FOLDS, Loader(), PARAMS, gate=LOOSE, sample=SAMPLE)
    finally:
        mp.undo()
    return rep, calls


def _same(a, b) -> bool:
    a, b = float(a), float(b)
    return (math.isnan(a) and math.isnan(b)) or a == b


def test_schema_rows_order(frame):
    assert tuple(frame.columns) == diag.DIAGNOSE_COLUMNS
    assert len(frame) == len(FOLDS) * 2 * len(PARAMS) == 16
    assert {c: str(t) for c, t in frame.dtypes.items()} == diag._DTYPES
    assert set(frame["phase"]) == {"train", "test"}
    keys = list(zip(frame["fold"], frame["phase"].map({"train": 0, "test": 1}), frame["strategy_id"],
                    frame["param_id"]))
    assert keys == sorted(keys)
    for r in frame.itertuples():
        p = BY_KEY[(r.strategy_id, r.param_id)]
        assert r.trigger == p.trigger and r.risk_pct == p.risk_pct
    assert frame["total_funding_xbt"].isna().all()  # 펀딩 끔 = 미반영 표시
    assert (frame["n_trades"] > 0).any()


def test_train_matches_run_walkforward(frame, walkforward):
    _, calls = walkforward
    for i, f in enumerate(FOLDS, 1):
        (span, summaries) = calls[i - 1]
        assert span == (f.train_start, f.train_end)
        sub = frame[(frame["fold"] == i) & (frame["phase"] == "train")]
        assert len(sub) == len(summaries)
        for r, s in zip(sub.to_dict("records"), summaries):
            assert (r["strategy_id"], r["param_id"]) == (s["strategy_id"], s["param_id"])
            assert r["n_trades"] == s["n_trades"]
            for k in diag._METRICS:
                assert _same(r[k], s[k]), (i, k)


def test_selection_matches_run_walkforward(frame, walkforward):
    rep, _ = walkforward
    for i, fr in enumerate(rep["folds"], 1):
        train = frame[(frame["fold"] == i) & (frame["phase"] == "train")].to_dict("records")
        sel = select_params(train, LOOSE)
        assert fr["selection"] is not None and sel is not None
        assert (sel["strategy_id"], sel["param_id"]) == (fr["selection"]["strategy_id"],
                                                         fr["selection"]["param_id"])
        test = frame[(frame["fold"] == i) & (frame["phase"] == "test") & (frame["strategy_id"] == sel["strategy_id"])
                     & (frame["param_id"] == sel["param_id"])].iloc[0]
        want = fr["test"]["default"]
        assert test["n_trades"] == want["n_trades"]
        for k in diag._METRICS:
            assert _same(test[k], want[k]), (i, k)


class ShortLoader(Loader):
    """구간 첫 3분만 돌려준다 → 모든 run 거래 0건."""

    def __call__(self, start, end):
        return super().__call__(start, start + pd.Timedelta(minutes=3))


def test_zero_trade_runs_keep_trigger():
    df = diag.diagnose_folds(FOLDS[:1], ShortLoader(), PARAMS, gate=LOOSE, sample=SAMPLE)
    assert (df["n_trades"] == 0).all()
    assert df["trigger"].notna().all() and df["strategy_id"].notna().all() and df["param_id"].notna().all()
    assert df["sharpe"].isna().all()


# CLI -------------------------------------------------------------------------------------------------

@pytest.fixture
def grid_file(tmp_path):
    p = tmp_path / "grid.json"
    p.write_text(json.dumps(GRID))
    return p


@pytest.fixture
def env(monkeypatch):
    """diag 모듈의 폴드·게이트와 `br.load_bars`(store_loader 경유)를 바꾼다. 로더를 돌려준다."""

    def setup(folds=None, loader=None):
        loader = loader or FakeLoadBars()
        monkeypatch.setattr(br, "load_bars", loader)
        monkeypatch.setattr(diag, "make_folds", folds or FoldsSpy())
        monkeypatch.setattr(diag, "resolve_gate", _loose_gate)
        return loader

    return setup


def _argv(out, grid, *extra, jobs=1):
    return ["--grid", str(grid), "--data-dir", "norm", "--out", str(out), "--jobs", str(jobs), *extra]


def _files(root):
    return sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file()) if root.exists() else []


def test_cli_writes_parquet(env, grid_file, tmp_path):
    loader = env()
    out = tmp_path / "out"
    assert diag.main(_argv(out, grid_file)) == 0
    assert _files(out) == [diag.diagnose_path(out).relative_to(out)]
    assert diag.diagnose_path(out) == out / "diagnose" / "folds.parquet"
    df = pd.read_parquet(diag.diagnose_path(out))
    assert tuple(df.columns) == diag.DIAGNOSE_COLUMNS and len(df) == 16
    assert len(loader.calls) == 4  # 폴드 2 × (학습, 검증), 표본 전체는 읽지 않음


def test_cli_funding(env, grid_file, tmp_path):
    env()
    fd = write_fund(tmp_path)
    out = tmp_path / "out"
    assert diag.main(_argv(out, grid_file, "--funding", "--funding-dir", str(fd))) == 0
    path = diag.diagnose_path(out, funded=True)
    assert path == out / "diagnose" / "folds+funding.parquet" and path.is_file()
    df = pd.read_parquet(path)
    assert np.isfinite(df["total_funding_xbt"]).all()
    assert (df["total_funding_xbt"] != 0).any()


@pytest.mark.parametrize("fold", [
    Fold(T("2021-07-01"), T("2022-01-01"), T("2022-01-01"), T("2022-03-01")),  # 검증이 OOS
    Fold(T("2021-01-01"), T("2021-07-01"), T("2021-07-01"), T("2022-01-02")),  # 검증 끝이 OOS 에 걸침
    Fold(T("2018-01-01"), T("2018-06-01"), T("2018-06-01"), T("2018-09-01")),  # 표본 시작 전
])
def test_sample_guard_exit_2_before_reading(env, grid_file, tmp_path, fold):
    loader = env(folds=FoldsSpy([fold]))
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as e:
        diag.main(_argv(out, grid_file))
    assert e.value.code == 2
    assert loader.calls == [] and _files(out) == []


def test_jobs2_equals_jobs1(env, grid_file, tmp_path):
    env()
    paths = []
    for jobs in (1, 2):
        out = tmp_path / f"j{jobs}"
        assert diag.main(_argv(out, grid_file, jobs=jobs)) == 0
        paths.append(diag.diagnose_path(out))
    assert paths[0].read_bytes() == paths[1].read_bytes()
    pdt.assert_frame_equal(pd.read_parquet(paths[0]), pd.read_parquet(paths[1]))


def test_runtime_failure_exit_1(env, grid_file, tmp_path):
    env(loader=FakeLoadBars(fail_on=2))
    out = tmp_path / "out"
    assert diag.main(_argv(out, grid_file)) == 1
    assert _files(out) == []  # parquet·.tmp 없음


def test_jobs_zero_exit_2(env, grid_file, tmp_path):
    env()
    with pytest.raises(SystemExit) as e:
        diag.main(_argv(tmp_path / "out", grid_file, jobs=0))
    assert e.value.code == 2
