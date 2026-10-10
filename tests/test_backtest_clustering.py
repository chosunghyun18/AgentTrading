"""DSR 군집(`src/backtest/clustering.py`) 테스트 — 합성 데이터만 쓴다."""

import time

import numpy as np
import pandas as pd
import pytest

from src.backtest import clustering as cl
from src.backtest.walkforward import sr_variance


def _blocks(n_blocks=3, per=10, days=300, noise=0.2, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2019-01-01", periods=days, tz="UTC")
    f = rng.normal(0, 0.01, (days, n_blocks))
    cols = {}
    for b in range(n_blocks):
        for i in range(per):
            cols[f"b{b}|{i:02d}"] = f[:, b] + rng.normal(0, 0.01 * noise, days) + 0.0005 * (b - 1)
    df = pd.DataFrame(cols, index=idx)
    return df, (df.mean() / df.std(ddof=1))


def test_block_structure_recovers_three():
    df, sr = _blocks()
    r = cl.cluster_trials(df, sr)
    assert r.effective_trials == 3
    assert r.n_input == r.n_clustered == 30
    assert len(r.representatives) == 3
    assert not r.hit_max_k
    for b in range(3):                                   # 같은 블록 = 같은 군집
        assert r.labels[[f"b{b}|{i:02d}" for i in range(10)]].nunique() == 1
    assert np.isfinite(r.cluster_var) and np.isfinite(r.silhouette_score)


def test_exact_duplicates_do_not_change_n():
    df, sr = _blocks()
    base = cl.cluster_trials(df, sr)
    dup = df[[f"b0|{i:02d}" for i in range(5)] + ["b2|03"]].copy()
    dup.columns = [f"dup{i}" for i in range(dup.shape[1])]
    big = pd.concat([df, dup], axis=1)
    sr2 = big.mean() / big.std(ddof=1)
    r = cl.cluster_trials(big, sr2)
    assert r.effective_trials == base.effective_trials == 3
    assert r.n_clustered == 36
    assert r.labels["dup0"] == r.labels["b0|00"]


def test_exhausted_like_run_is_clustered_not_dropped():
    df, _ = _blocks()
    ex = df["b1|00"].copy()
    ex.iloc[150:] = 0.0                                  # 소진: 이후 수익률 0
    df = df.copy()
    df["exhausted"] = ex
    sr = df.mean() / df.std(ddof=1)
    r = cl.cluster_trials(df, sr)
    assert r.n_clustered == 31 and "exhausted" in r.labels.index
    assert r.effective_trials >= 3


def test_zero_variance_runs_are_singletons_and_dedup():
    df, sr = _blocks()
    df = df.copy()
    df["flat1"] = 0.0
    df["flat2"] = 0.0                                    # 같은 상수 → 하나로
    df["flat3"] = 0.001                                  # 다른 상수 → 별도
    df["allnan"] = np.nan                                # 결측 0 채움 → flat1 과 동일
    sr = sr.copy()
    for c in ("flat1", "flat2", "flat3", "allnan"):
        sr[c] = np.nan
    r = cl.cluster_trials(df, sr)
    assert r.effective_trials == 3 + 2
    assert r.labels["flat1"] == r.labels["flat2"] == r.labels["allnan"] != r.labels["flat3"]
    assert r.n_clustered == 34


def test_column_order_invariance():
    df, sr = _blocks(seed=7, noise=0.8)
    a = cl.cluster_trials(df, sr)
    perm = np.random.default_rng(3).permutation(df.shape[1])
    b = cl.cluster_trials(df.iloc[:, perm], sr)
    assert a.effective_trials == b.effective_trials
    assert a.labels.to_dict() == b.labels.to_dict()
    assert a.representatives == b.representatives
    assert a.cluster_var == b.cluster_var or (np.isnan(a.cluster_var) and np.isnan(b.cluster_var))
    c = cl.cluster_trials(df, sr)
    assert c.labels.to_dict() == a.labels.to_dict()      # 재실행 동일


def test_representative_closest_to_median_and_v():
    idx = pd.date_range("2020-01-01", periods=6, tz="UTC")
    rng = np.random.default_rng(0)
    f1 = rng.normal(0, 1, 6)
    f2 = rng.normal(0, 1, 6)
    df = pd.DataFrame({"a": f1, "b": f1 * 2 + 1e-9 * np.arange(6), "c": f1 * 3 + 2e-9 * np.arange(6),
                       "x": f2, "y": f2 * 5 + 1e-9 * np.arange(6)}, index=idx)
    sr = pd.Series({"a": 0.10, "b": 0.30, "c": 0.35, "x": -0.20, "y": -0.10})
    r = cl.cluster_trials(df, sr)
    assert r.effective_trials == 2
    # 군집 {a,b,c}: 중앙값 0.30 → b. 군집 {x,y}: 중앙값 −0.15, x·y 거리 같음 → 정렬상 앞 x
    assert r.representatives == ["b", "x"]
    assert r.cluster_var == pytest.approx(sr_variance([{"sr_daily": 0.30}, {"sr_daily": -0.20}]))


def test_degenerate_cases():
    idx = pd.date_range("2020-01-01", periods=50, tz="UTC")
    rng = np.random.default_rng(0)
    base = rng.normal(0, 1, 50)
    # 전부 상관 ≥ 0.99 → 1군집, V NaN
    df = pd.DataFrame({f"r{i}": base + rng.normal(0, 0.01, 50) for i in range(6)}, index=idx)
    r = cl.cluster_trials(df, df.mean() / df.std())
    assert r.effective_trials == 1 and np.isnan(r.cluster_var) and np.isnan(r.silhouette_score)
    # 열 1개
    r = cl.cluster_trials(df[["r0"]], pd.Series({"r0": 0.1}))
    assert r.effective_trials == 1 and np.isnan(r.cluster_var) and r.representatives == ["r0"]
    # 열 2개, 무상관 → 2군집
    two = pd.DataFrame({"p": base, "q": rng.normal(0, 1, 50)}, index=idx)
    r = cl.cluster_trials(two, pd.Series({"p": 0.1, "q": 0.2}))
    assert r.effective_trials == 2 and r.cluster_var == pytest.approx(0.005)
    # 열 0개
    r = cl.cluster_trials(pd.DataFrame(index=idx), pd.Series(dtype=float))
    assert r.effective_trials == 0 and np.isnan(r.cluster_var)


def test_correlation_distance():
    idx = pd.date_range("2020-01-01", periods=5, tz="UTC")
    x = np.array([1.0, 2, 3, 4, 5])
    d = cl.correlation_distance(pd.DataFrame({"a": x, "b": 2 * x, "c": -x}, index=idx))
    assert d.loc["a", "b"] == pytest.approx(0.0, abs=1e-7)
    assert d.loc["a", "c"] == pytest.approx(1.0)
    assert (np.diag(d.to_numpy()) == 0).all()


def test_max_k_cap_flag():
    df, sr = _blocks()
    r = cl.cluster_trials(df, sr, max_k=2)
    assert r.hit_max_k                                   # 최적이 상한에 닿음 → 호출자가 경고


def test_runtime_800_runs():
    rng = np.random.default_rng(5)
    days, n, nf = 1400, 800, 25
    f = rng.normal(0, 0.01, (days, nf))
    load = rng.integers(0, nf, n)
    x = f[:, load] + rng.normal(0, 0.012, (days, n))
    df = pd.DataFrame(x, index=pd.date_range("2018-03-01", periods=days, tz="UTC"),
                      columns=[f"s{i // 3}|p{i % 3}" for i in range(n)])
    sr = df.mean() / df.std(ddof=1)
    t0 = time.perf_counter()
    r = cl.cluster_trials(df, sr)
    dt = time.perf_counter() - t0
    print(f"runtime {dt:.1f}s N={r.effective_trials} hit={r.hit_max_k}")
    assert dt < 180
    assert 2 <= r.effective_trials <= 800
