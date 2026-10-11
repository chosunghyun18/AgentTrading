"""환경변수 설정 한 곳(`AT_*`) — 배포자가 바꾸는 값의 목록·기본값·해석.

- 비밀값(거래소 API 키)은 여기서 다루지 않는다. 키 주입은 `src/agent/settings.load_env()`(`.env` → 환경) 그대로다.
- 이 모듈은 `.env` 를 환경에 **주입하지 않고** 읽기만 한다(`dotenv_values`) — 테스트가 로컬 `.env` 의 값에 오염되지 않게.
  우선순위: 환경변수 → `.env` → 기본값. 빈 문자열은 "미설정"으로 보고 다음 단계로 넘어간다.
- 기본값은 지금 코드와 같다(로컬 1인 사용: 127.0.0.1·인증 없음·볼트 미연결).
설계: Obsidian `Projects/work/AgentTrading/design/platform-architecture.md` 3.1절 "설정 단일화".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"

MODES = ("backtest", "shadow", "paper", "live")
MODE_ALIASES = {"demo": "paper"}  # 기존 state.json·감사 로그·콘솔 코드의 "demo"(Bybit demo trading) = paper
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

# 이름 → (기본값, 설명). 기본값 "" 은 "끔/미설정".
VARS: dict[str, tuple[str, str]] = {
    "AT_OUT_DIR": (str(ROOT / "data" / "out" / "backtest"), "백테스트 산출물 폴더"),
    "AT_DATA_DIR": (str(ROOT / "data" / "raw" / "normalized" / "bitmex"), "정규화 1분봉·체결 폴더"),
    "AT_SYMBOL": ("XBTUSD", "백테스트 심볼(BitMEX 인버스 테이프)"),
    "AT_TRADE_SYMBOL": ("BTCUSDT", "실행 심볼(Bybit linear) — 콘솔·에이전트"),
    "AT_AGENT_DIR": (str(ROOT / "data" / "agent"), "에이전트 상태·감사 로그 폴더"),
    "AT_RISK_FILE": (str(ROOT / "config" / "risk.yaml"), "계정 리스크 한도 파일(사용자 값, 커밋 금지)"),
    "AT_VAULT_DIR": ("", "볼트 프로젝트 폴더(선택). 비면 볼트 연동(사람 할 일·Phase 진행 카드) 끔"),
    "AT_LIVE_ENABLED": ("0", "LIVE 잠금 해제 플래그(기존 `src/agent/settings.py` 가 읽는다)"),
    "AT_BIND_ADDRESS": ("127.0.0.1", "관리 화면 bind 주소(`python -m src.ui.serve`). loopback 이 아니면 인증이 필요하다"),
    "AT_PORT": ("8501", "관리 화면 포트"),
    "AT_AUTH_REQUIRED": ("0", "1 이면 OIDC 로그인(`st.login`) + 허용 이메일 필수"),
    "AT_AUTH_ALLOWED_EMAILS": ("", "허용 이메일(쉼표 구분, 대소문자 무시). AT_AUTH_REQUIRED=1 이면 비어 있을 수 없다"),
    "AT_AUTH_EXTERNAL": ("0", "1 이면 앞단(리버스 프록시 인증·SSH 터널)이 접근을 막는다고 운영자가 선언 — 외부 bind 허용"),
}


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str]:
    """`.env` 의 값(환경 주입 없음). 파일이 없으면 빈 dict."""
    try:
        return {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}
    except OSError:
        return {}


def reload() -> None:
    """`.env` 캐시를 비운다(테스트·파일 변경 후)."""
    _dotenv.cache_clear()


def get(name: str) -> str:
    """환경변수 → `.env` → 기본값. 모르는 이름은 KeyError(목록은 `VARS`)."""
    default, _ = VARS[name]
    for source in (os.environ, _dotenv()):
        v = source.get(name)
        if v is not None and v.strip() != "":
            return v.strip()
    return default


def flag(name: str) -> bool:
    return get(name).lower() in TRUE_VALUES


def path(name: str) -> Path | None:
    """경로 설정. 비어 있으면 None(끔)."""
    v = get(name)
    return Path(v).expanduser() if v else None


def normalize_mode(mode: str) -> str:
    """`demo` 같은 별칭을 정식 모드로 바꾼다. 모르는 값은 ValueError."""
    m = MODE_ALIASES.get(mode, mode)
    if m not in MODES:
        raise ValueError(f"모드는 {MODES} 중 하나(별칭 {tuple(MODE_ALIASES)}): {mode!r}")
    return m


def allowed_emails() -> frozenset[str]:
    return frozenset(e.strip().lower() for e in get("AT_AUTH_ALLOWED_EMAILS").split(",") if e.strip())


@dataclass(frozen=True)
class AuthPolicy:
    required: bool          # OIDC 로그인 + 허용 이메일 인가
    external: bool          # 앞단 인증 선언(리버스 프록시·SSH 터널)
    allowed: frozenset[str]


def auth_policy() -> AuthPolicy:
    return AuthPolicy(required=flag("AT_AUTH_REQUIRED"), external=flag("AT_AUTH_EXTERNAL"), allowed=allowed_emails())


def is_loopback(address: str | None) -> bool:
    return (address or "").strip().lower() in LOOPBACK


def bind_allowed(address: str | None, policy: AuthPolicy) -> tuple[bool, str]:
    """loopback 이 아닌 주소는 인증 요구(허용 목록 포함) 또는 외부 인증 선언이 있어야 한다 — fail-closed.

    주소가 비어 있으면(None·"") Streamlit 기본값인 "모든 인터페이스"로 보고 같은 규칙을 적용한다.
    """
    shown = address if address else "(비어 있음 = 모든 인터페이스)"
    if is_loopback(address):
        return True, f"{address} 는 loopback — 인증 없이 허용"
    if policy.required:
        if not policy.allowed:
            return False, ("AT_AUTH_REQUIRED=1 인데 AT_AUTH_ALLOWED_EMAILS 가 비어 있다 — 아무도 들어올 수 없으므로 "
                           "기동하지 않는다")
        return True, f"{shown} 외부 bind — OIDC 로그인 + 허용 이메일 {len(policy.allowed)}명"
    if policy.external:
        return True, f"{shown} 외부 bind — 앞단 인증 선언(AT_AUTH_EXTERNAL=1)"
    return False, (f"{shown} 는 loopback 이 아닌데 인증이 없다. AT_AUTH_REQUIRED=1(+AT_AUTH_ALLOWED_EMAILS) 또는 "
                   "AT_AUTH_EXTERNAL=1 을 설정하거나 AT_BIND_ADDRESS=127.0.0.1 로 둔다")
