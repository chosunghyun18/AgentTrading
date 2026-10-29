"""`src/strategy/builtin/*.py` 의 `STRATEGY` 자동 발견."""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache

from src.shared.schema import ENTRY_REASONS
from src.strategy import builtin
from src.strategy.base import STATUSES, Strategy


def check(s: Strategy) -> Strategy:
    if not s.id or not s.id.replace("_", "").isalnum():
        raise ValueError(f"전략 id 는 영숫자·_ : {s.id!r}")
    if s.status not in STATUSES:
        raise ValueError(f"{s.key}: status 는 {sorted(STATUSES)}: {s.status!r}")
    if s.entry_reason not in ENTRY_REASONS:
        raise ValueError(f"{s.key}: entry_reason {s.entry_reason!r} 가 schema.ENTRY_REASONS 에 없다")
    return s


def discover(modules=None) -> dict[str, Strategy]:
    """id → 전략. 같은 id 가 둘이면 ValueError."""
    if modules is None:
        modules = [importlib.import_module(f"{builtin.__name__}.{m.name}")
                   for m in pkgutil.iter_modules(builtin.__path__)]
    out: dict[str, Strategy] = {}
    for mod in modules:
        s = getattr(mod, "STRATEGY", None)
        if s is None:
            continue
        check(s)
        if s.id in out:
            raise ValueError(f"전략 id 중복: {s.id}")
        out[s.id] = s
    return out


@lru_cache(maxsize=1)
def _all() -> dict[str, Strategy]:
    return discover()


def all() -> list[Strategy]:  # noqa: A001
    return list(_all().values())


def get(strategy_id: str) -> Strategy:
    try:
        return _all()[strategy_id]
    except KeyError:
        raise KeyError(f"모르는 전략: {strategy_id!r} (등록 {sorted(_all())})") from None
