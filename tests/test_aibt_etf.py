"""ETF 일봉 엔진 — 규칙 5종의 체결 시점·금액, TWR 단위가치, 공통 시작일 자르기, 벤치마크."""

import numpy as np
import pandas as pd
import pytest

from src.aibt import etf
from src.aibt import spec as S
from tests import aibt_fixtures as fx

BASE = {"title": "", "start": "", "end": "", "initial_cash": 10000, "monthly_contribution": 0, "benchmark": "", "rule": "buy_and_hold",
        "weights": [{"symbol": "A", "weight": 1}], "signal_symbol": "", "ma_window": 0, "hold_symbol": "", "else_symbol": "",
        "ref_symbol": "", "stages": [], "vehicle": "", "check": "daily", "reset_years": 0, "reserve_fraction": 0,
        "rebalance_period": "monthly"}


@pytest.fixture(scope="module")
def prices():
    return fx.series_set()


@pytest.fixture(scope="module")
def url(tmp_path_factory, prices):
    return fx.write_sqlite(tmp_path_factory.mktemp("db") / "p.db", prices)


def run(prices, **kw):
    cat = {s: (prices[s].first_valid_index().date(), prices[s].last_valid_index().date()) for s in prices}
    sp = S.validate({**BASE, **kw}, cat)
    return etf.run(sp, prices)


def month_firsts(idx):
    key = idx.year * 12 + idx.month
    return idx[1:][np.asarray(key[1:]) != np.asarray(key[:-1])]


def test_catalog_and_load_prices_sqlite(url, prices):
    cat = etf.catalog(url)
    assert set(cat) == {"A", "B", "C", "D"} and cat["D"][0] == prices.index[60].date()
    px = etf.load_prices(url, ["A", "D"])
    assert list(px.columns) == ["A", "D"] and px["D"].isna().sum() == 60
    np.testing.assert_allclose(px["A"].to_numpy(), prices["A"].to_numpy())


def test_buy_and_hold_unit_equals_price_ratio(prices):
    r = run(prices)
    a = prices["A"]
    np.testing.assert_allclose(r.unit.to_numpy(), (a / a.iloc[0]).to_numpy(), rtol=1e-12)
    assert len(r.trades) == 1 and r.metrics["multiple"] == pytest.approx(r.metrics["unit_multiple"])
    assert r.metrics["contributed"] == 10000 and r.notes == [] and r.benchmark_unit is None


def test_dca_contributions_and_twr(prices):
    r = run(prices, rule="dca", monthly_contribution=500)
    mf = month_firsts(prices.index)
    assert r.metrics["contributed"] == pytest.approx(10000 + 500 * len(mf))
    a = prices["A"]
    np.testing.assert_allclose(r.unit.to_numpy(), (a / a.iloc[0]).to_numpy(), rtol=1e-12)  # 적립이 단위가치를 바꾸지 않는다
    shares = 10000 / a.iloc[0] + sum(500 / a[d] for d in mf)
    assert r.equity.iloc[-1] == pytest.approx(shares * a.iloc[-1])
    assert set(r.trades["date"].iloc[1:]) == set(mf)


def test_rebalance_identical_series_no_trades_and_period_dates(prices):
    same = prices.assign(E=prices["A"])
    r = run(same, rule="rebalance", weights=[{"symbol": "A", "weight": 0.5}, {"symbol": "E", "weight": 0.5}], rebalance_period="monthly")
    assert len(r.trades) == 2  # 시작 매수 2건만 — 같은 경로라 리밸런싱 차이가 0
    r2 = run(prices, rule="rebalance", weights=[{"symbol": "A", "weight": 0.5}, {"symbol": "C", "weight": 0.5}], rebalance_period="quarterly")
    later = r2.trades[r2.trades["date"] > prices.index[0]]
    q = prices.index.year * 4 + (prices.index.month - 1) // 3
    qf = set(prices.index[1:][np.asarray(q[1:]) != np.asarray(q[:-1])])
    assert later["date"].nunique() == len(qf) and set(later["date"]) == qf
    assert set(later["action"]) == {"매수", "매도"}


def test_ma_filter_switches_next_day_and_flat_in_cash(prices):
    r = run(prices, rule="ma_filter", weights=[], signal_symbol="A", ma_window=5, hold_symbol="A", else_symbol="")
    a = prices["A"]
    ma = a.rolling(5).mean()
    px = a.loc[r.unit.index]
    assert r.unit.index[0] == ma.first_valid_index() and any("5일선" in n for n in r.notes)
    below = px.index[(px <= ma.loc[px.index]).to_numpy()]
    first_sell = r.trades[r.trades["action"] == "매도"]["date"].iloc[0]
    assert first_sell == px.index[px.index.get_loc(below[0]) + 1]  # 종가 판정 → 다음 거래일 체결
    # 현금 보유 구간에서는 단위가치가 변하지 않는다
    sells = r.trades[r.trades["action"] == "매도"]["date"]
    buys = r.trades[(r.trades["action"] == "매수") & (r.trades["date"] > first_sell)]["date"]
    seg = r.unit.loc[sells.iloc[0]:buys.iloc[0]]
    assert seg.nunique() == 1


def test_drawdown_trigger_stages_once_per_cycle_daily(prices):
    r = run(prices, rule="drawdown_trigger", weights=[{"symbol": "C", "weight": 1}], ref_symbol="A", vehicle="B",
            reserve_fraction=0.3, check="daily",
            stages=[{"drawdown_pct": 10, "cumulative_fraction": 0.5}, {"drawdown_pct": 30, "cumulative_fraction": 1.0}])
    a = prices["A"]
    dd = a / a.cummax() - 1
    t1 = dd.index[(dd <= -0.10).to_numpy()][0]
    t2 = dd.index[(dd <= -0.30).to_numpy()][0]
    buys = r.trades[r.trades["symbol"] == "B"]
    assert list(buys["date"]) == [a.index[a.index.get_loc(t1) + 1], a.index[a.index.get_loc(t2) + 1]]
    assert list(buys["amount"]) == pytest.approx([1500.0, 1500.0])  # 예비 3000 의 50% → 누적 100%
    assert list(buys["reason"]) == ["1단계 트리거", "2단계 트리거"]
    assert r.trades.iloc[0]["amount"] == pytest.approx(7000.0)  # 기본 보유 C 70%
    assert r.metrics["mdd"] < 0 and r.metrics["n_buys"] == 3


def test_drawdown_trigger_eom_waits_for_month_end(prices):
    r = run(prices, rule="drawdown_trigger", weights=[{"symbol": "C", "weight": 1}], ref_symbol="A", vehicle="B",
            reserve_fraction=0.3, check="eom", stages=[{"drawdown_pct": 10, "cumulative_fraction": 1.0}])
    a = prices["A"]
    dd = a / a.cummax() - 1
    key = a.index.year * 12 + a.index.month
    eom = a.index[:-1][np.asarray(key[:-1]) != np.asarray(key[1:])]
    first_eom_hit = [d for d in eom if dd[d] <= -0.10][0]
    buy = r.trades[r.trades["symbol"] == "B"]["date"].iloc[0]
    assert buy == a.index[a.index.get_loc(first_eom_hit) + 1]
    assert (r.trades["symbol"] == "B").sum() == 1


def test_drawdown_trigger_resets_on_new_high(prices):
    # 회복 뒤 두 번째 하락을 만든다: 경로 끝에 하락 구간 추가
    a2 = np.concatenate([prices["A"].to_numpy(), prices["A"].to_numpy()[-1] * np.cumprod(np.full(40, 0.99))])
    idx = pd.bdate_range(prices.index[0], periods=len(a2))
    p2 = pd.DataFrame({"A": a2, "B": fx.leveraged(a2, 3.0), "C": 100.0}, index=idx)
    r = run(p2, rule="drawdown_trigger", weights=[{"symbol": "C", "weight": 1}], ref_symbol="A", vehicle="B",
            reserve_fraction=0.3, check="daily", stages=[{"drawdown_pct": 10, "cumulative_fraction": 0.5}])
    buys = r.trades[r.trades["symbol"] == "B"]
    assert len(buys) == 2 and buys["date"].iloc[1] > prices.index[-1]
    assert buys["amount"].iloc[1] == pytest.approx(0.5 * (3000 - 1500))  # 두 번째 사이클 풀 = 남은 예비 현금


def test_common_start_and_clipping_notes(prices):
    r = run(prices, weights=[{"symbol": "A", "weight": 0.5}, {"symbol": "D", "weight": 0.5}], start="2019-01-01", end="2030-01-01")
    assert r.unit.index[0] == prices.index[60] and r.unit.index[-1] == prices.index[-1]
    assert len(r.notes) == 2 and "공통 시작일" in r.notes[0]


def test_benchmark_and_yearly(prices):
    r = run(prices, rule="dca", monthly_contribution=100, weights=[{"symbol": "B", "weight": 1}], benchmark="A")
    a = prices["A"]
    np.testing.assert_allclose(r.benchmark_unit.to_numpy(), (a / a.iloc[0]).to_numpy(), rtol=1e-12)
    assert list(r.yearly.columns) == ["전략", "A"] and set(r.yearly.index) == set(prices.index.year.unique())
    assert set(r.metrics["benchmark"]) == {"multiple", "unit_multiple", "cagr", "mdd", "vol"}
    assert r.metrics["benchmark"]["unit_multiple"] == pytest.approx(a.iloc[-1] / a.iloc[0])


def test_window_too_short_or_missing_symbol(prices):
    with pytest.raises(ValueError, match="2일 미만"):
        run(prices, start=str(prices.index[-1].date()))
    with pytest.raises(ValueError, match="없는 종목"):
        sp = S.validate({**BASE, "weights": [{"symbol": "A", "weight": 1}]}, {"A": (prices.index[0].date(), prices.index[-1].date())})
        etf.run(sp, prices[["B"]])
