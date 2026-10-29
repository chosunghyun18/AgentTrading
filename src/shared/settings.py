"""환경변수 설정 한 곳(`AT_*`) — 배포자가 바꾸는 값의 목록·기본값·해석.

- 비밀값(거래소 API 키)은 여기서 다루지 않는다. 키 주입은 `src/agent/settings.load_env()`(`.env` → 환경) 그대로다.
- 이 모듈은 `.env` 를 환경에 **주입하지 않고** 읽기만 한다(`dotenv_values`) — 테스트가 로컬 `.env` 의 값에 오염되지 않게.
  우선순위: 환경변수 → `.env` → 기본값. 빈 문자열은 "미설정"으로 보고 다음 단계로 넘어간다.
- `ENV_ONLY` 에 든 4개는 아직 `src/agent/settings.py` 가 **환경변수만** 읽는다(`.env` 값은 `load_env()` 주입 뒤에만 반영,
  `AT_LIVE_ENABLED` 는 정확히 "1" 만 해제). 목록·기본값만 여기서 함께 관리하고, 읽기 경로 통합은 T-03 이후(W1) 로 미룬다.
- 기본값은 지금 코드와 같다(로컬 1인 사용: 127.0.0.1·인증 없음·볼트 미연결).
설계: Obsidian `Projects/work/AgentTrading/design/platform-architecture.md` 3.1절 "설정 단일화".
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"

MODES = ("backtest", "shadow", "paper", "live")
MODE_ALIASES = {"demo": "paper"}  # 기존 state.json·감사 로그·콘솔 코드의 "demo"(Bybit demo trading) = paper
LOOPBACK_NAMES = frozenset({"localhost"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})

# 이름 → (기본값, 설명). 기본값 "" 은 "끔/미설정".
VARS: dict[str, tuple[str, str]] = {
    "AT_OUT_DIR": (str(ROOT / "data" / "out" / "backtest"), "백테스트 산출물 폴더"),
    "AT_DATA_DIR": (str(ROOT / "data" / "raw" / "normalized" / "bitmex"), "정규화 1분봉·체결 폴더"),
    "AT_SYMBOL": ("XBTUSD", "백테스트 심볼(BitMEX 인버스 테이프)"),
    "AT_TRADE_SYMBOL": ("BTCUSDT", "실행 심볼(Bybit linear) — 콘솔·에이전트 (환경변수만, `src/agent/settings.py`)"),
    "AT_AGENT_DIR": (str(ROOT / "data" / "agent"), "에이전트 상태·감사 로그 폴더 (환경변수만, `src/agent/settings.py`)"),
    "AT_STORE_PATH": (str(ROOT / "data" / "agent" / "platform.sqlite"), "플랫폼 저장소(SQLite) — 에이전트·run·자본곡선·이벤트·명령"),
    "AT_RISK_FILE": (str(ROOT / "config" / "risk.yaml"), "계정 리스크 한도 파일(사용자 값, 커밋 금지) (환경변수만)"),
    "AT_VAULT_DIR": ("", "볼트 프로젝트 폴더(선택). 비면 볼트 연동(사람 할 일·Phase 진행 카드) 끔"),
    "AT_LIVE_ENABLED": ("0", "LIVE 잠금 해제 — 정확히 \"1\" 일 때만(환경변수만, `src/agent/settings.live_unlocked`)"),
    "AT_BIND_ADDRESS": ("127.0.0.1", "관리 화면 bind 주소(`python -m src.ui.serve`). loopback 이 아니면 인증이 필요하다"),
    "AT_PORT": ("8501", "관리 화면 포트(런처 bind 포트. compose 에서는 host 쪽 공개 포트)"),
    "AT_AUTH_REQUIRED": ("0", "1 이면 OIDC 로그인(`st.login`) + 허용 이메일 필수"),
    "AT_AUTH_ALLOWED_EMAILS": ("", "허용 이메일(쉼표 구분, 대소문자 무시). 비어 있으면 아무도 들어오지 못한다"),
    "AT_AUTH_EXTERNAL": ("0", "1 이면 앞단(리버스 프록시 인증·SSH 터널)이 접근을 막는다고 운영자가 선언 — 외부 bind 허용"
                               "(AT_AUTH_REQUIRED=1 이 함께 켜져 있으면 REQUIRED 가 우선)"),
}
ENV_ONLY = frozenset({"AT_TRADE_SYMBOL", "AT_AGENT_DIR", "AT_RISK_FILE", "AT_LIVE_ENABLED"})


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
    """켬/끔 플래그. 1·true·yes·on → True, 0·false·no·off·빈값 → False. 그 밖의 값(`enabled` 등)은 ValueError —
    보안 플래그를 조용히 끄거나 켜지 않는다."""
    v = get(name).lower()
    if v in TRUE_VALUES:
        return True
    if v in FALSE_VALUES:
        return False
    raise ValueError(f"{name}={v!r} 는 알 수 없는 값이다. 1/0·true/false·yes/no·on/off 중 하나를 쓴다")


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
    """플래그 값이 이상하면 ValueError 를 그대로 올린다(호출처가 기동 거부·화면 안내로 바꾼다)."""
    return AuthPolicy(required=flag("AT_AUTH_REQUIRED"), external=flag("AT_AUTH_EXTERNAL"), allowed=allowed_emails())


def is_loopback(address: str | None) -> bool:
    """127.0.0.0/8·::1(어떤 표기든)·localhost 만 loopback. 비어 있거나 모르는 값은 아니다."""
    a = (address or "").strip().lower()
    if not a:
        return False
    if a in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(a.strip("[]")).is_loopback
    except ValueError:
        return False


def bind_allowed(address: str | None, policy: AuthPolicy) -> tuple[bool, str]:
    """loopback 이 아닌 주소는 인증 요구 또는 외부 인증 선언이 있어야 기동한다 — fail-closed.

    - 주소가 비어 있으면(None·"") Streamlit 기본값인 "모든 인터페이스"로 보고 같은 규칙을 적용한다.
    - 인증 요구(REQUIRED=1)인데 허용 목록이 비어 있으면 기동은 하되 아무도 통과하지 못한다(화면이 설정 안내를 보인다) —
      컨테이너가 재시작 루프에 빠지지 않고 운영자가 무엇을 채울지 보게 하기 위해서다.
    """
    shown = address if address else "(비어 있음 = 모든 인터페이스)"
    if is_loopback(address):
        return True, f"{address} 는 loopback — 인증 없이 허용"
    if policy.required:
        both = " (AT_AUTH_EXTERNAL=1 도 켜져 있으나 REQUIRED 가 우선)" if policy.external else ""
        if not policy.allowed:
            return True, (f"{shown} 외부 bind — OIDC 로그인 요구. AT_AUTH_ALLOWED_EMAILS 가 비어 있어 아무도 통과하지 "
                          f"못한다(화면에 설정 안내){both}")
        return True, f"{shown} 외부 bind — OIDC 로그인 + 허용 이메일 {len(policy.allowed)}명{both}"
    if policy.external:
        return True, f"{shown} 외부 bind — 앞단 인증 선언(AT_AUTH_EXTERNAL=1)"
    return False, (f"{shown} 는 loopback 이 아닌데 인증이 없다. AT_AUTH_REQUIRED=1(+AT_AUTH_ALLOWED_EMAILS, secrets.toml) 또는 "
                   "AT_AUTH_REQUIRED=0 AT_AUTH_EXTERNAL=1(앞단 인증) 을 설정하거나 AT_BIND_ADDRESS=127.0.0.1 로 둔다")
