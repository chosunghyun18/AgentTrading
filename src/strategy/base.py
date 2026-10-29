"""전략 계약: 의도(`Signal`)만 내고 수량·주문은 플랫폼이 정한다."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

WINDOW_COLUMNS = ("ts", "open", "high", "low", "close")
STATUSES = frozenset({"example", "candidate", "verified"})


@dataclass(frozen=True)
class Signal:
    """다음 봉 open 시장가 진입 의도. 손절은 진입가 대비 비율, 익절은 손절폭의 R 배(None = 없음)."""

    side: str
    stop_frac: float
    tp_r: float | None = None
    max_hold: int | None = None  # 봉 수. None = 시간 청산 없음

    def __post_init__(self):
        if self.side not in ("long", "short"):
            raise ValueError(f"side 는 long|short: {self.side!r}")
        if not (isinstance(self.stop_frac, (int, float)) and 0 < self.stop_frac < 1):
            raise ValueError(f"stop_frac 은 (0, 1): {self.stop_frac!r}")
        if self.tp_r is not None and not (math.isfinite(self.tp_r) and self.tp_r > 0):
            raise ValueError(f"tp_r 은 > 0 또는 None: {self.tp_r!r}")
        if self.max_hold is not None and (not isinstance(self.max_hold, int) or self.max_hold < 1):
            raise ValueError(f"max_hold 는 1 이상 정수 또는 None: {self.max_hold!r}")


class Strategy:
    """기반 클래스. 하위 클래스가 클래스 속성과 `warmup`·`on_bar` 를 채운다.

    `on_bar(window, p)` 의 window 는 현재 봉까지 최근 `warmup(p)` 봉([`ts`,`open`,`high`,`low`,`close`],
    인덱스 0..)뿐이라 미래 봉을 볼 수 없다. 포지션이 없고 진입 대기도 없을 때만 불린다.
    """

    id: str = ""
    version: str = "1"
    status: str = "example"
    entry_reason: str = ""
    summary: str = ""
    defaults: Mapping[str, object] = {}

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    def params(self, raw: Mapping | None = None) -> dict:
        """기본값 + raw 덮어쓰기. 모르는 키·타입 불일치는 ValueError. 마지막에 `validate`."""
        raw = dict(raw or {})
        unknown = set(raw) - set(self.defaults)
        if unknown:
            raise ValueError(f"{self.key}: 모르는 파라미터 {sorted(unknown)}")
        p = dict(self.defaults)
        for k, v in raw.items():
            d = self.defaults[k]
            if isinstance(v, bool) or (d is not None and isinstance(d, (int, float)) and not isinstance(v, (int, float))):
                raise ValueError(f"{self.key}: {k} 는 수여야 한다: {v!r}")
            if isinstance(d, int) and not isinstance(d, bool) and (not float(v).is_integer()):
                raise ValueError(f"{self.key}: {k} 는 정수여야 한다: {v!r}")
            p[k] = type(d)(v) if d is not None else v
        self.validate(p)
        return p

    def validate(self, p: dict) -> None:
        """파라미터 상호 조건. 위반이면 ValueError."""

    def param_id(self, p: Mapping) -> str:
        return ";".join(f"{k}={p[k]}" for k in sorted(p))

    def params_hash(self, p: Mapping) -> str:
        return hashlib.sha256(json.dumps(dict(p), sort_keys=True).encode()).hexdigest()[:16]

    def warmup(self, p: Mapping) -> int:
        raise NotImplementedError

    def on_bar(self, window: pd.DataFrame, p: Mapping) -> Signal | None:
        raise NotImplementedError
