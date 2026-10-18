"""Contract units for W1. Linear replay tables remain a W2 concern."""
from __future__ import annotations

import math
from numbers import Real
from typing import Protocol


def positive(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


def validate_side(side: str) -> None:
    if side not in ("long", "short"):
        raise ValueError("side must be long or short")


def sizing_inputs(equity, price, side, stop_frac, risk_frac, max_exposure, max_qty):
    validate_side(side)
    for name, value in locals().copy().items():
        if name != "side":
            positive(name, value)
    if stop_frac >= 1:
        raise ValueError("stop_frac must be below 1")
    if max_exposure > 10:
        raise ValueError("max_exposure must not exceed 10")


class Contract(Protocol):
    name: str
    ccy: str

    def size(self, equity: float, price: float, side: str, stop_frac: float,
             risk_frac: float, *, max_exposure: float = 4.0,
             max_qty: float = 10_000_000) -> tuple[float, bool]: ...
    def pnl(self, side: str, qty: float, entry: float, exit: float) -> float: ...
    def leverage(self, qty: float, price: float, equity: float) -> float: ...
    def notional(self, qty: float, price: float) -> float: ...
