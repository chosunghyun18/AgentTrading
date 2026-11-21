"""명세 검증 — 허용·거부·왕복·한 줄 요약."""

from datetime import date

import pytest

from src.aibt import spec as S

CAT = {"A": (date(2020, 1, 1), date(2021, 1, 1)), "B": (date(2020, 1, 1), date(2021, 1, 1)), "C": (date(2020, 1, 1), date(2021, 1, 1))}
BASE = {"title": "", "start": "", "end": "", "initial_cash": 10000, "monthly_contribution": 0, "benchmark": "", "rule": "buy_and_hold",
        "weights": [{"symbol": "a", "weight": 1}], "signal_symbol": "", "ma_window": 0, "hold_symbol": "", "else_symbol": "",
        "ref_symbol": "", "stages": [], "vehicle": "", "check": "daily", "reset_years": 0, "reserve_fraction": 0,
        "rebalance_period": "monthly"}


def spec(**kw):
    return S.validate({**BASE, **kw}, CAT)


def test_buy_and_hold_defaults_and_title():
    sp = spec()
    assert sp.rule == "buy_and_hold" and sp.weights == {"A": 1.0} and sp.start is None and sp.initial_cash == 10000
    assert sp.title == S.describe(sp) and "단순 보유 A 100%" in sp.title
    assert sp.symbols == ["A"]


def test_weights_normalized_and_merged():
    sp = spec(weights=[{"symbol": "A", "weight": 0.6}, {"symbol": "B", "weight": 0.2}, {"symbol": "B", "weight": 0.2}])
    assert sp.weights == {"A": pytest.approx(0.6), "B": pytest.approx(0.4)}


def test_drawdown_trigger_full():
    sp = spec(rule="drawdown_trigger", start="2020-02-01", end="2020-12-01", monthly_contribution=100, benchmark="A",
              weights=[{"symbol": "C", "weight": 1}], ref_symbol="A", vehicle="B", check="eom", reset_years=3, reserve_fraction=0.3,
              stages=[{"drawdown_pct": 10, "cumulative_fraction": 0.5}, {"drawdown_pct": 30, "cumulative_fraction": 1.0}])
    assert sp.stages == (S.Stage(10, 0.5), S.Stage(30, 1.0)) and sp.check == "eom" and sp.reset_years == 3
    assert sp.symbols == ["C", "A", "B"]
    d = S.describe(sp)
    assert "직전 3년 최고" in d and "−10% → 누적 50%" in d and "월말 종가" in d and "벤치마크 A" in d


def test_roundtrip_to_dict():
    sp = spec(rule="ma_filter", signal_symbol="A", ma_window=200, hold_symbol="B", else_symbol="", weights=[], start="2020-03-01")
    assert S.validate(sp.to_dict(), CAT) == sp
    assert set(sp.to_dict()) == set(S.SCHEMA["properties"])


@pytest.mark.parametrize("kw,msg", [
    ({"rule": "nope"}, "rule"),
    ({"extra": 1}, "모르는 키"),
    ({"weights": [{"symbol": "A", "weight": 0.5}, {"symbol": "B", "weight": 0.3}]}, "합이 1"),
    ({"weights": [{"symbol": "ZZZ", "weight": 1}]}, "데이터에 없다"),
    ({"weights": []}, "하나 이상"),
    ({"start": "2020-06-01", "end": "2020-01-01"}, "빠르지 않다"),
    ({"start": "2020/06/01"}, "YYYY-MM-DD"),
    ({"initial_cash": 0}, "initial_cash"),
    ({"monthly_contribution": -5}, "monthly_contribution"),
    ({"rule": "dca"}, "dca"),
    ({"rule": "ma_filter", "signal_symbol": "A", "hold_symbol": "B", "ma_window": 1}, "ma_window"),
    ({"rule": "ma_filter", "signal_symbol": "A", "hold_symbol": "B", "else_symbol": "B", "ma_window": 20}, "같다"),
    ({"rule": "ma_filter", "hold_symbol": "B", "ma_window": 20}, "signal_symbol"),
    ({"rule": "drawdown_trigger", "ref_symbol": "A", "vehicle": "B", "reserve_fraction": 0.3,
      "stages": [{"drawdown_pct": 30, "cumulative_fraction": 0.5}, {"drawdown_pct": 10, "cumulative_fraction": 1}]}, "증가"),
    ({"rule": "drawdown_trigger", "ref_symbol": "A", "vehicle": "B", "reserve_fraction": 0.3,
      "stages": [{"drawdown_pct": 10, "cumulative_fraction": 0.5}, {"drawdown_pct": 30, "cumulative_fraction": 0.5}]}, "증가"),
    ({"rule": "drawdown_trigger", "ref_symbol": "A", "vehicle": "B", "reserve_fraction": 1.0,
      "stages": [{"drawdown_pct": 10, "cumulative_fraction": 1}]}, "reserve_fraction"),
    ({"rule": "drawdown_trigger", "ref_symbol": "A", "vehicle": "B", "reserve_fraction": 0.3, "stages": []}, "stages"),
    ({"rule": "drawdown_trigger", "ref_symbol": "A", "vehicle": "B", "reserve_fraction": 0.3, "check": "weekly",
      "stages": [{"drawdown_pct": 10, "cumulative_fraction": 1}]}, "check"),
    ({"rule": "rebalance", "rebalance_period": "daily"}, "rebalance_period"),
    ({"benchmark": "ZZZ"}, "benchmark"),
])
def test_rejects(kw, msg):
    with pytest.raises(S.SpecError) as ei:
        spec(**kw)
    assert any(msg in e for e in ei.value.errors), ei.value.errors


def test_collects_multiple_errors():
    with pytest.raises(S.SpecError) as ei:
        spec(initial_cash=0, start="bad", weights=[{"symbol": "ZZZ", "weight": 2}])
    assert len(ei.value.errors) >= 3
