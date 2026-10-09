"""콘솔 설정: `.env` 의 API 키·LIVE 잠금, 운용 모드(`state.json`), 상태 폴더 경로.

키는 화면에서 받지 않는다 — `.env` 에만 둔다. 화면에는 `masked()` 뒤 4자리만 보인다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
MODES = ("demo", "live")
ENV_KEYS = {"demo": ("BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET"),
            "live": ("BYBIT_LIVE_API_KEY", "BYBIT_LIVE_API_SECRET")}
LIVE_FLAG = "AT_LIVE_ENABLED"


def load_env() -> None:
    """프로젝트 루트 `.env` 를 읽는다(이미 있는 환경변수는 덮지 않음)."""
    load_dotenv(ROOT / ".env", override=False)


def agent_dir() -> Path:
    return Path(os.environ.get("AT_AGENT_DIR") or ROOT / "data" / "agent")


def risk_file() -> Path:
    return Path(os.environ.get("AT_RISK_FILE") or ROOT / "config" / "risk.yaml")


def symbol() -> str:
    return os.environ.get("AT_TRADE_SYMBOL", "BTCUSDT")


@dataclass(frozen=True)
class Keys:
    api_key: str
    api_secret: str = field(repr=False)

    def masked(self) -> str:
        return "…" + self.api_key[-4:] if len(self.api_key) >= 4 else "…"


def keys(mode: str) -> Keys | None:
    k, s = (os.environ.get(n, "").strip() for n in ENV_KEYS[mode])
    return Keys(k, s) if k and s else None


def live_unlocked() -> bool:
    """LIVE 전환 전제: `.env` 에 `AT_LIVE_ENABLED=1` 과 LIVE 키 둘 다."""
    return os.environ.get(LIVE_FLAG) == "1" and keys("live") is not None


def _state_path() -> Path:
    return agent_dir() / "state.json"


def get_mode() -> str:
    """저장된 모드. 없거나 LIVE 인데 잠금이 풀려 있지 않으면 demo."""
    try:
        mode = json.loads(_state_path().read_text(encoding="utf-8")).get("mode", "demo")
    except (OSError, ValueError):
        mode = "demo"
    if mode not in MODES or (mode == "live" and not live_unlocked()):
        return "demo"
    return mode


def set_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"모드는 {MODES} 중 하나: {mode!r}")
    if mode == "live" and not live_unlocked():
        raise PermissionError(f"LIVE 잠금: .env 에 {LIVE_FLAG}=1 과 LIVE API 키가 필요하다")
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    os.replace(tmp, p)
