"""에이전트 = 전략 + 파라미터 + 심볼 + 모드 + 한도. YAML 한 장.

모드는 shadow(주문 없음)·paper(Bybit demo) 만. **live 는 거부한다** — 실제 돈은 사람 승인 뒤 별도 단계(W4).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.agent.risk import HARD_MAX_LEVERAGE
from src.strategy import registry

MODES = ("shadow", "paper")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


@dataclass(frozen=True)
class AgentConfig:
    id: str
    strategy: str
    symbol: str
    mode: str
    params: dict = field(default_factory=dict)
    risk_frac: float = 0.01
    max_leverage: float = 2.0

    def __post_init__(self):
        if not ID_RE.match(self.id):
            raise ValueError(f"에이전트 id 는 소문자·숫자·-_ (최대 48자): {self.id!r}")
        if self.mode == "live":
            raise ValueError("live 모드는 지원하지 않는다(사람 승인 단계 W4). shadow·paper 만")
        if self.mode not in MODES:
            raise ValueError(f"mode 는 {MODES}: {self.mode!r}")
        if not re.fullmatch(r"[A-Z0-9]{3,20}", self.symbol):
            raise ValueError(f"symbol 형식: {self.symbol!r}")
        if not 0 < self.risk_frac <= 0.05:
            raise ValueError(f"risk_frac 은 (0, 0.05]: {self.risk_frac!r}")
        if not 0 < self.max_leverage <= HARD_MAX_LEVERAGE:
            raise ValueError(f"max_leverage 는 (0, {HARD_MAX_LEVERAGE:g}]: {self.max_leverage!r}")
        s = registry.get(self.strategy)
        object.__setattr__(self, "params", s.params(self.params))

    def to_dict(self) -> dict:
        return {"id": self.id, "strategy": self.strategy, "symbol": self.symbol, "mode": self.mode,
                "params": dict(self.params), "risk_frac": self.risk_frac, "max_leverage": self.max_leverage}


KEYS = {"id", "strategy", "symbol", "mode", "params", "risk_frac", "max_leverage"}


def from_mapping(d: Mapping) -> AgentConfig:
    unknown = set(d) - KEYS
    if unknown:
        raise ValueError(f"모르는 키: {sorted(unknown)}")
    missing = {"id", "strategy", "mode"} - set(d)
    if missing:
        raise ValueError(f"필수 키 없음: {sorted(missing)}")
    return AgentConfig(id=str(d["id"]), strategy=str(d["strategy"]), symbol=str(d.get("symbol", "BTCUSDT")),
                       mode=str(d["mode"]), params=dict(d.get("params") or {}),
                       risk_frac=float(d.get("risk_frac", 0.01)), max_leverage=float(d.get("max_leverage", 2.0)))


def load(path: Path | str) -> AgentConfig:
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(d, Mapping):
        raise ValueError("에이전트 YAML 최상위는 매핑이어야 한다")
    return from_mapping(d)
