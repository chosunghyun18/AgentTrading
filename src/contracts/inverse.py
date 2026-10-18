"""USD inverse contract; settlement and equity are XBT."""
from __future__ import annotations

import math

from src.analysis.synthetic import (
    MAX_EXPOSURE, MAX_ORDER_QTY, RISK_GUARD_PCT, inverse_pnl, position_size,
)
from src.contracts import positive, sizing_inputs, validate_side


class Inverse:
    name = "inverse"
    ccy = "XBT"

    def size(self, equity, price, side, stop_frac, risk_frac, *,
             max_exposure=MAX_EXPOSURE, max_qty=MAX_ORDER_QTY):
        sizing_inputs(equity, price, side, stop_frac, risk_frac, max_exposure, max_qty)
        if max_exposure == MAX_EXPOSURE and max_qty == MAX_ORDER_QTY:
            return position_size(equity, price, side, stop_frac * 100, risk_frac * 100)
        factor = 1 - stop_frac if side == "long" else 1 + stop_frac
        requested = risk_frac * equity * price * factor / stop_frac
        cap = min(max_exposure * equity * price, max_qty, MAX_ORDER_QTY)
        qty = math.floor(min(requested, cap))
        stop = price * factor
        if -inverse_pnl(side, qty, price, stop) > RISK_GUARD_PCT / 100 * equity:
            raise ValueError("planned loss exceeds 30% of equity")
        return qty, requested > cap

    def pnl(self, side, qty, entry, exit):
        validate_side(side)
        positive("qty", qty)
        positive("entry", entry)
        positive("exit", exit)
        return inverse_pnl(side, qty, entry, exit)

    def leverage(self, qty, price, equity):
        positive("equity", equity)
        positive("price", price)
        return self.notional(qty, price) / price / equity

    def notional(self, qty, price):
        positive("qty", qty)
        positive("price", price)
        return float(qty)
