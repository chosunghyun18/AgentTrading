"""W1 최소 — 전략 계약·레지스트리·엔진(v1 상태기계와 동치)·에이전트 설정."""

import types

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from src.analysis import synthetic
from src.analysis.features import compute_features
from src.shared.schema import ENTRY_REASONS
from src.strategy import agent, registry, testkit
from src.strategy.base import Signal, Strategy
from src.strategy.engine import batch_run

SMA = registry.get("sma_cross")


def test_registry_discovers_example():
    assert [s.id for s in registry.all()] == ["sma_cross"]
    assert SMA.status == "example" and SMA.key == "sma_cross@1"
    with pytest.raises(KeyError):
        registry.get("nope")


def test_registry_rejects_duplicate_and_unknown_reason():
    mod = types.SimpleNamespace(STRATEGY=SMA)
    with pytest.raises(ValueError, match="중복"):
        registry.discover([mod, mod])
    bad = type("Bad", (Strategy,), {"id": "bad", "entry_reason": "nope"})()
    with pytest.raises(ValueError, match="ENTRY_REASONS"):
        registry.discover([types.SimpleNamespace(STRATEGY=bad)])


def test_entry_reasons_additive():
    assert {"h1_breakout", "h2_momentum", "h3_meanrev", "c1_meanrev_limit", "c1_meanrev_market",
            "sma_cross"} == set(ENTRY_REASONS)


def test_params_validation():
    assert SMA.params({"fast": 5})["fast"] == 5
    for raw in ({"fast": 40}, {"x": 1}, {"fast": 2.5}, {"fast": "3"}, {"stop_pct": 0}, {"fast": True}):
        with pytest.raises(ValueError):
            SMA.params(raw)


def test_signal_validation():
    for kw in ({"side": "up", "stop_frac": 0.01}, {"side": "long", "stop_frac": 1.5},
               {"side": "long", "stop_frac": 0.01, "tp_r": -1}, {"side": "long", "stop_frac": 0.01, "max_hold": 0}):
        with pytest.raises(ValueError):
            Signal(**kw)


def test_contract_checks_pass():
    assert testkit.check_all(SMA) == []
    assert testkit.check_all(SMA, {"fast": 3, "slow": 8}, testkit.synthetic_bars(1500, seed=3)) == []


def test_batch_run_requires_continuous_bars():
    b = testkit.synthetic_bars(100)
    with pytest.raises(ValueError, match="연속"):
        batch_run(SMA, {}, b.drop(index=50))


class Replay(Strategy):
    """v1 의 신호 배열을 ts 로 그대로 내는 전략 — 엔진 상태기계를 v1 `generate_run` 과 비교한다."""
    id = "replay"
    status = "example"

    def __init__(self, sig: dict, prm):
        self.sig, self.prm, self.entry_reason = sig, prm, prm.entry_reason
        self.defaults = {}

    def warmup(self, p):
        return 1

    def on_bar(self, window, p):
        side = self.sig.get(window["ts"].iloc[-1])
        if side is None:
            return None
        return Signal(side, self.prm.stop_pct / 100, self.prm.tp_r, self.prm.max_hold)


@pytest.mark.parametrize("prm", [
    synthetic.Params("h1", 20, 0.5, 2.0, 60, 1.0),
    synthetic.Params("h2", 10, 1.0, None, 30, 2.0, k=0.5),
    synthetic.Params("h3", 30, 0.3, 1.0, 15, 1.0, k=1.0),
])
def test_engine_matches_v1_state_machine(prm):
    bars = testkit.synthetic_bars(4000, seed=11)
    feats = compute_features(bars, windows=(prm.n,))
    long_sig, short_sig = synthetic.entry_signals(bars, feats, prm)
    sig = {t: ("long" if lg else "short") for t, lg, sh in zip(bars["ts"], long_sig, short_sig) if lg or sh}
    want = synthetic.generate_run(bars, prm).roundtrips
    got = batch_run(Replay(sig, prm), {}, bars, risk_frac=prm.risk_pct / 100).roundtrips
    assert len(want) > 10
    cols = [c for c in want.columns if c not in ("strategy_id", "param_id")]
    pdt.assert_frame_equal(got[cols], want[cols], check_exact=False, rtol=1e-9)


def test_engine_hand_scenario_stop_and_end_of_data():
    """신호 봉 다음 open 진입 → 봉 내 손절 → 마지막 봉 close 청산."""
    ts = pd.date_range("2019-06-01", periods=6, freq="1min", tz="UTC")
    bars = testkit.synthetic_bars(6)
    bars["ts"] = ts
    bars[["open", "high", "low", "close"]] = [[100, 101, 99, 100], [100, 100.5, 99.5, 100], [100, 100.2, 98, 99],
                                              [99, 99.5, 98.5, 99], [99, 99.5, 98.5, 99], [99, 99.5, 98.5, 99.2]]
    sig = {ts[0]: "long", ts[3]: "short"}
    prm = types.SimpleNamespace(stop_pct=1.0, tp_r=None, max_hold=100, entry_reason="sma_cross")
    rt = batch_run(Replay(sig, prm), {}, bars).roundtrips
    assert list(rt["exit_reason"]) == ["stop", "end_of_data"]
    assert rt["entry_ts"].iloc[0] == ts[1] and rt["exit_price"].iloc[0] == pytest.approx(99.0)
    assert rt["entry_ts"].iloc[1] == ts[4] and rt["exit_price"].iloc[1] == pytest.approx(99.2)


def test_agent_config():
    cfg = agent.from_mapping({"id": "a1", "strategy": "sma_cross", "mode": "shadow", "params": {"fast": 5}})
    assert cfg.params["fast"] == 5 and cfg.params["slow"] == 30 and cfg.symbol == "BTCUSDT"
    bad = [{"mode": "live"}, {"mode": "demo"}, {"risk_frac": 0.2}, {"max_leverage": 20}, {"params": {"fast": 99}},
           {"strategy": "nope"}, {"id": "Bad Id"}, {"extra": 1}]
    for b in bad:
        with pytest.raises((ValueError, KeyError)):
            agent.from_mapping({"id": "a1", "strategy": "sma_cross", "mode": "shadow", **b})


def test_agent_yaml(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("id: a2\nstrategy: sma_cross\nmode: paper\nrisk_frac: 0.005\n", encoding="utf-8")
    assert agent.load(p).risk_frac == 0.005
    p.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        agent.load(p)


def test_no_judgement_path_imports():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "src"
    for pkg in ("strategy", "store", "runtime", "cli"):
        for f in (root / pkg).rglob("*.py"):
            text = "\n".join(ln for ln in f.read_text(encoding="utf-8").splitlines()
                              if ln.lstrip().startswith(("import ", "from ")))
            for bad in ("backtest.run import", "run_c1", "backtest import run", "src.agent.live"):
                assert bad not in text, f"{f}: {bad}"
