"""ui.charts 변환 — 트레이스 수·데이터·색이 입력과 토큰을 따르는지(AppTest 로는 Plotly 내용을 볼 수 없어 여기서)."""

import numpy as np
import pandas as pd

from src.ui import charts, theme

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def _folds():
    return pd.DataFrame({"폴드": [1, 2], "default net": [0.1, -0.05], "bybit net": [0.08, -0.07],
                         "학습 Sharpe": [2.0, np.nan], "검증 Sharpe": [0.5, -1.0]})


def test_equity_fig():
    d = pd.date_range(T("2020-01-01"), periods=3, freq="D")
    eq = pd.DataFrame({"date": list(d) * 2, "profile": ["default"] * 3 + ["bybit"] * 3,
                       "equity": [1.0, 1.1, 1.2, 1.0, 1.05, 1.1], "daily_ret": 0.0})
    f = charts.equity_fig(eq)
    assert [t.name for t in f.data] == ["default", "bybit"]
    assert list(f.data[0].y) == [1.0, 1.1, 1.2]
    assert f.data[0].line.color == theme.LINE_COLORS["default"]


def test_fold_and_train_test_figs():
    f = charts.fold_net_fig(_folds())
    assert list(f.data[0].x) == ["F1", "F2"] and list(f.data[1].y) == [0.08, -0.07]
    g = charts.train_test_fig(_folds())
    assert [t.name for t in g.data] == ["학습", "검증"] and np.isnan(g.data[0].y[1])


def test_trigger_pie():
    r = pd.DataFrame({"trigger": ["h1", "h2"], "n_runs": [10, 20], "n_pass": [1, 3], "ratio": [0.1, 0.15]})
    f = charts.trigger_pie_fig(r)
    assert list(f.data[0].values) == [1, 3] and list(f.data[0].labels) == ["h1", "h2"]
    empty = charts.trigger_pie_fig(r.assign(n_pass=0))
    assert len(empty.data) == 0 and empty.layout.annotations[0].text == "통과 run 없음"


def test_scatter_splits_pass():
    runs = pd.DataFrame({"param_id": list("abc"), "n_trades": [100, 5, 200], "sharpe": [1.5, 3.0, 0.2],
                         "mdd": [0.1, 0.05, 0.4], "gate": ["pass", "insufficient", "fail"]})
    f = charts.scatter_fig(runs, {"min_sharpe": 1.0, "max_drawdown": 0.3})
    other, passed = f.data
    assert list(passed.y) == [1.5] and passed.marker.color == theme.SUCCESS
    assert len(other.y) == 2
    assert {s.type for s in f.layout.shapes} == {"line"} and len(f.layout.shapes) == 2


def test_candle_markers():
    bars = pd.DataFrame({"ts": pd.date_range(T("2020-03-01"), periods=5, freq="1min"), "open": 1.0, "high": 2.0,
                         "low": 0.5, "close": 1.5})
    trade = {"side": "short", "entry_ts": T("2020-03-01 00:01"), "entry_price": 1.2, "exit_ts": T("2020-03-01 00:03"),
             "exit_price": 1.0, "stop_price": 1.4, "tp_price": float("nan")}
    f = charts.candle_fig(bars, trade)
    assert [t.type for t in f.data] == ["candlestick", "scatter", "scatter"]
    assert f.data[1].marker.symbol == "triangle-down" and list(f.data[2].y) == [1.0]
    assert len(f.layout.shapes) == 1  # 손절만(익절 NaN 은 그리지 않음)


def test_diagnose_figs():
    stats = pd.DataFrame({"폴드": [1, 1, 2, 2], "트리거": ["h1", "h2", "h1", "h2"],
                          "Sharpe 중앙(r1)": [-1.0, 0.5, -2.0, 0.1]})
    f = charts.trigger_sharpe_fig(stats, "학습")
    assert [t.name for t in f.data] == ["h1", "h2"] and list(f.data[1].x) == ["F1", "F2"]
    pts = pd.DataFrame({"strategy_id": "s", "param_id": ["a", "b", "c"], "trigger": ["h1", "h2", "h2"],
                        "risk_pct": [1.0, 1.0, 2.0], "train_sharpe": [1.0, 2.0, 3.0], "test_sharpe": [0.5, -1.0, 0.0],
                        "candidate": True})
    g = charts.divergence_fig(pts, 1, ("s", "b"))
    assert [t.name for t in g.data] == ["h1 (1)", "h2 (1)", "선택"]  # r1 만, risk 2 제외
    assert list(g.data[2].x) == [2.0] and len(g.layout.shapes) == 1  # y=x 선
    assert len(charts.divergence_fig(pts, 1, ("s", "zz")).data) == 2
    ero = pd.DataFrame({"폴드": ["F1", "전체", "F1", "전체"], "구간": ["학습", "학습", "검증", "검증"],
                        "gross>0": [0.6, 0.6, 0.5, 0.5], "net>0": [0.1, 0.1, 0.05, 0.05]})
    h = charts.erosion_fig(ero, "검증")
    assert [t.name for t in h.data] == ["gross>0", "net>0"] and list(h.data[0].x) == ["F1"]
