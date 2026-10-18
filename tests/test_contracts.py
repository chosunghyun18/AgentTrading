"""W1 contract units: manual arithmetic, legacy identity, and cap safety."""
import math

import pytest

from src.analysis.synthetic import inverse_pnl, position_size
from src.contracts.inverse import Inverse
from src.contracts.linear import Linear


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("equity,price,stop,risk", [
    (1.0, 10_000.0, .01, .01), (.001, 20_000.0, .005, .02),
    (1e8, 10_000.0, .02, .05),
])
def test_inverse_matches_legacy(side, equity, price, stop, risk):
    contract = Inverse()
    assert contract.size(equity, price, side, stop, risk) == position_size(
        equity, price, side, stop * 100, risk * 100)
    assert contract.pnl(side, 100, price, price * 1.1) == inverse_pnl(side, 100, price, price * 1.1)


def test_inverse_units_and_caps():
    contract = Inverse()
    assert contract.notional(1000, 10_000) == 1000
    assert contract.leverage(1000, 10_000, .1) == 1
    assert contract.pnl("long", 1000, 10_000, 20_000) == .05
    assert contract.size(1, 10_000, "long", .01, .01, max_qty=7) == (7, True)
    assert contract.size(1, 10_000, "long", .01, .01, max_exposure=.5) == (5000, True)
    assert contract.size(1e-12, 1, "long", .01, .01)[0] == 0


@pytest.mark.parametrize("side", ["long", "short"])
def test_linear_manual_arithmetic(side):
    contract = Linear(qty_step=.01)
    # 1% of 1000 USDT at a 2% stop on a 100 USDT entry: five units.
    assert contract.size(1000, 100, side, .02, .01) == (5.0, False)
    assert contract.size(1000, 100, side, .02, .01, max_exposure=.2) == (2.0, True)
    assert contract.size(1000, 100, side, .02, .01, max_qty=1.239) == (1.23, True)
    assert contract.notional(5, 100) == 500
    assert contract.leverage(5, 100, 1000) == .5
    assert contract.pnl(side, 5, 100, 110) == (50 if side == "long" else -50)


def test_linear_decimal_step_and_tiny_order():
    contract = Linear(qty_step=.1)
    assert contract.size(3, 10, "long", .1, .1, max_qty=.3) == (.3, False)
    assert contract.size(1, 100_000, "short", .1, .01) == (0.0, False)


@pytest.mark.parametrize("factory", [Inverse, Linear])
@pytest.mark.parametrize("name,value", [
    ("equity", 0), ("price", -1), ("stop_frac", 1), ("risk_frac", math.nan),
    ("max_qty", math.inf), ("max_exposure", 11), ("risk_frac", True), ("side", "buy"),
])
def test_bad_sizing_inputs(factory, name, value):
    args = dict(equity=1, price=100, side="long", stop_frac=.01, risk_frac=.01)
    args[name] = value
    with pytest.raises(ValueError):
        factory().size(**args)


@pytest.mark.parametrize("value", [0, -1, math.inf, math.nan, True])
def test_linear_bad_step(value):
    with pytest.raises(ValueError):
        Linear(qty_step=value)


@pytest.mark.parametrize("factory", [Inverse, Linear])
def test_planned_loss_guard(factory):
    with pytest.raises(ValueError):
        factory().size(1, 100, "long", .2, .5, max_exposure=10)
