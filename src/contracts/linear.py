"""Linear contract arithmetic only; no replay or exchange execution."""
from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR, localcontext

from src.contracts import positive, sizing_inputs, validate_side


class Linear:
    name = "linear"
    ccy = "USDT"

    def __init__(self, qty_step=0.001):
        self.qty_step = positive("qty_step", qty_step)

    def size(self, equity, price, side, stop_frac, risk_frac, *,
             max_exposure=4.0, max_qty=10_000_000):
        sizing_inputs(equity, price, side, stop_frac, risk_frac, max_exposure, max_qty)
        # Decimal avoids rounding an order above its risk/exposure/quantity cap.
        with localcontext() as context:
            context.prec = 50
            e, p, s, r, x, cap, step = map(
                lambda v: Decimal(str(v)),
                (equity, price, stop_frac, risk_frac, max_exposure, max_qty, self.qty_step),
            )
            requested = r * e / (p * s)
            ceiling = min(x * e / p, cap)
            qty = (min(requested, ceiling) / step).to_integral_value(rounding=ROUND_FLOOR) * step
            if qty * p * s > Decimal("0.30") * e:
                raise ValueError("planned loss exceeds 30% of equity")
            return float(qty), requested > ceiling

    def pnl(self, side, qty, entry, exit):
        validate_side(side)
        positive("qty", qty)
        positive("entry", entry)
        positive("exit", exit)
        return qty * (exit - entry) * (1 if side == "long" else -1)

    def leverage(self, qty, price, equity):
        positive("equity", equity)
        return self.notional(qty, price) / equity

    def notional(self, qty, price):
        return positive("qty", qty) * positive("price", price)
