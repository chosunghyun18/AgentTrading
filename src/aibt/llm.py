"""자연어 → 명세 JSON 번역. AI 는 번역만 하고 계산하지 않는다.

공급자 2개를 지원한다(`AT_AI_PROVIDER`, 기본 `auto` — 무료를 먼저 본다):

| 공급자 | 키 | 비용 | 의존성 |
|---|---|---|---|
| `openrouter_free` | `OPENROUTER_API_KEY` | **무료 모델만**(`src/aibt/openrouter.py` 가 가격 0 을 강제·검증) | 없음(`requests`) |
| `anthropic` | `ANTHROPIC_API_KEY` | 유료 | optional extra `ai` |

무료 경로의 규칙은 볼트 `Projects/work/autodev/무료 LLM API 오픈 소스 조사와 로컬 프로젝트 공통 적용.md`(2026-10-17)
결정을 그대로 따른다. 무료 모델은 형식을 덜 지키므로 응답에서 JSON 을 너그럽게 꺼내고(`extract_json`),
그래도 실패하면 화면이 원문을 보여 사용자가 고치게 한다.

키는 환경변수 → `.env` 순으로 읽고(주입하지 않음) 값은 화면·로그에 내보내지 않는다.
설계: Obsidian `Projects/work/AgentTrading/task/ai-backtest.md`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from dotenv import dotenv_values

from src.aibt import openrouter
from src.aibt.spec import SCHEMA
from src.shared import settings

ANTHROPIC_KEY = "ANTHROPIC_API_KEY"
OPENROUTER_KEY = openrouter.KEY_NAME
PROVIDERS = ("openrouter_free", "anthropic")
PROVIDER_LABEL = {"openrouter_free": "OpenRouter 무료 모델", "anthropic": "Anthropic(유료)"}
MAX_TOKENS = 4096
BETAS = ["server-side-fallback-2026-07-01"]

SYSTEM = """당신은 ETF 백테스트 **명세 번역기**다. 사용자의 요청(한국어·영어)을 아래 규칙 중 하나의 명세 JSON 으로 바꾼다.
계산·예측·해설은 하지 않는다. JSON 객체 하나만 낸다. 설명·코드펜스·앞뒤 문장을 붙이지 않는다.

규칙(rule):
- buy_and_hold: 비중대로 사서 그대로 둔다. "그냥 들고 있기", "단순 보유".
- dca: 매월 첫 거래일에 monthly_contribution 을 비중대로 산다. "적립식", "매달 N 씩".
- ma_filter: signal_symbol 종가가 ma_window 일 이동평균 위면 hold_symbol, 아래면 else_symbol(빈 문자열 = 현금). "200일선 위에서만".
- drawdown_trigger: ref_symbol 이 기준 고점 대비 stages 의 drawdown_pct 만큼 빠지면 예비 현금(reserve_fraction)의 cumulative_fraction 까지
  vehicle 을 산다(누적, 단계당 사이클에 1회, 수단은 팔지 않음). weights 는 예비 현금을 뺀 기본 보유 비중. "−30% 빠지면 TQQQ 산다".
  check 는 판정 주기(daily 일별 종가 / eom 월말 종가). reset_years 0 = 시작일 이후 사상 최고 기준, N = 직전 N년 최고.
- rebalance: 비중을 rebalance_period 마다 되돌린다. "60/40 매년 리밸런싱".

채우기 규칙:
- 모든 키를 반드시 낸다. 그 규칙에서 안 쓰는 키는 "" · 0 · [] · "daily" · "monthly" 로 둔다.
- weights 는 [{"symbol": "QQQ", "weight": 0.6}, ...] 형식이고 weight 합은 1. 언급 없으면 균등.
- initial_cash 언급 없으면 10000. monthly_contribution 없으면 0(dca 는 반드시 > 0). 통화 단위는 신경 쓰지 않는다.
- start·end 는 YYYY-MM-DD. "2015년부터" → "2015-01-01". 언급 없으면 "". 데이터 범위 밖이면 그대로 두어도 된다(엔진이 자른다).
- 종목은 카탈로그에 있는 코드만 쓴다. 카탈로그: QQQ 나스닥100 1배, QLD 2배, TQQQ 3배, XLK S&P 기술섹터 1배, TECL 기술섹터 3배.
  "나스닥 3배" = TQQQ, "기술주 3배" = TECL. 카탈로그에 없는 종목을 요구하면 가장 가까운 것으로 바꾸고 title 에 적는다.
- benchmark 는 비교할 단순 보유 종목 1개. 사용자가 비교 대상을 말하지 않으면 ""(단, 레버리지 ETF 전략이면 그 기초 1배 종목을 넣는다).
- title 은 한국어 한 줄.
- 이전 명세가 대화에 있으면 그것을 기준으로 사용자가 바꾸라고 한 부분만 바꾼다."""


# ── 키·공급자 ────────────────────────────────────────────────────────────────────────────────────

def env_value(name: str) -> str:
    """환경변수 → `.env`. 비면 ""(값은 돌려주기만 하고 환경에 주입하지 않는다)."""
    v = os.environ.get(name, "").strip()
    if v:
        return v
    try:
        return (dotenv_values(settings.ENV_FILE).get(name) or "").strip()
    except OSError:
        return ""


def api_key() -> str:
    """Anthropic 키(기존 이름 유지)."""
    return env_value(ANTHROPIC_KEY)


def free_key() -> str:
    return env_value(OPENROUTER_KEY)


def sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def ready(provider: str) -> bool:
    if provider == "openrouter_free":
        return bool(free_key())
    return bool(api_key()) and sdk_available()


def resolve_provider(explicit: str = "") -> str:
    """`explicit` → `AT_AI_PROVIDER` → auto(무료 먼저). 모르는 값은 ValueError."""
    name = (explicit or settings.get("AT_AI_PROVIDER")).strip().lower()
    if name == "auto":
        return next((p for p in PROVIDERS if ready(p)), PROVIDERS[0])
    if name not in PROVIDERS:
        raise ValueError(f"AT_AI_PROVIDER 는 auto·{'·'.join(PROVIDERS)} 중 하나: {name!r}")
    return name


def available() -> bool:
    return any(ready(p) for p in PROVIDERS)


def model(provider: str = "") -> str:
    provider = provider or resolve_provider()
    return settings.get("AT_AI_FREE_MODEL") if provider == "openrouter_free" else settings.get("AT_AI_MODEL")


def status() -> dict:
    """화면 안내용 — 어떤 공급자가 쓰이고 무엇이 없는지. 키 값은 담지 않는다."""
    try:
        p = resolve_provider()
        err = ""
    except ValueError as e:
        p, err = PROVIDERS[0], str(e)
    return {"provider": p, "label": PROVIDER_LABEL[p], "model": model(p), "ready": ready(p), "error": err,
            "free_ready": ready("openrouter_free"), "paid_ready": ready("anthropic"),
            "sdk": sdk_available(), "free_key": OPENROUTER_KEY, "paid_key": ANTHROPIC_KEY}


# ── 메시지 ───────────────────────────────────────────────────────────────────────────────────────

def catalog_text(catalog: dict) -> str:
    lines = [f"- {s}: {a} ~ {b}" for s, (a, b) in sorted(catalog.items())]
    return "사용 가능한 종목과 데이터 범위:\n" + "\n".join(lines)


def build_messages(turns: list[dict], catalog: dict) -> list[dict]:
    """대화 턴(role·content) → API messages. 첫 사용자 턴 앞에 카탈로그를 붙인다(캐시 안정: 시스템은 고정, 카탈로그는 메시지)."""
    out = []
    for k, t in enumerate(turns):
        content = t["content"]
        if k == 0:
            content = f"{catalog_text(catalog)}\n\n요청: {content}"
        out.append({"role": t["role"], "content": content})
    return out


# ── 응답에서 JSON 꺼내기 ─────────────────────────────────────────────────────────────────────────

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)


def _loads(s: str):
    try:
        return json.loads(s)
    except ValueError:
        return None


def _first_object(s: str) -> str | None:
    """문자열 리터럴을 건너뛰며 중괄호 짝을 맞춰 첫 JSON 객체 구간을 잘라낸다."""
    start = s.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def extract_json(text: str) -> dict | None:
    """무료 모델이 코드펜스·설명을 붙여도 명세 객체를 꺼낸다. 객체가 아니면 None."""
    s = (text or "").strip()
    if not s:
        return None
    for candidate in (s, *(m.group(1) for m in _FENCE.finditer(s))):
        obj = _loads(candidate)
        if isinstance(obj, dict):
            return obj
    inner = _first_object(s)
    if inner is not None:
        obj = _loads(inner)
        if isinstance(obj, dict):
            return obj
    return None


# ── 호출 ─────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class ParseResult:
    raw: dict | None
    text: str = ""
    error: str = ""
    model: str = ""
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)
    provider: str = ""


def _anthropic_client():
    import anthropic
    return anthropic.Anthropic(api_key=api_key())


def _usage(u) -> dict:
    g = lambda k: int(getattr(u, k, 0) or 0)  # noqa: E731
    return {"input": g("input_tokens"), "cache_read": g("cache_read_input_tokens"),
            "cache_write": g("cache_creation_input_tokens"), "output": g("output_tokens")}


def _result(raw_text: str, **kw) -> ParseResult:
    """응답 텍스트 → ParseResult(JSON 추출 포함)."""
    obj = extract_json(raw_text)
    if obj is None:
        return ParseResult(None, text=raw_text, error="응답에서 명세 JSON 을 찾지 못했다", **kw)
    return ParseResult(obj, text=raw_text, **kw)


def _parse_anthropic(turns: list[dict], catalog: dict, *, client=None, model_id: str) -> ParseResult:
    try:
        client = client or _anthropic_client()
    except ImportError:
        return ParseResult(None, error="anthropic SDK 가 설치되지 않았다 — `uv sync --extra ai`", provider="anthropic")
    try:
        resp = client.beta.messages.create(
            model=model_id,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=build_messages(turns, catalog),
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=BETAS,
            fallbacks="default",
        )
    except Exception as e:  # SDK 예외 종류는 지연 import 뒤에만 알 수 있다 — 이름으로 분류한다
        name = type(e).__name__
        hint = {"AuthenticationError": "API 키가 잘못됐다", "RateLimitError": "요청 한도에 걸렸다 — 잠시 뒤 다시",
                "APIConnectionError": "네트워크 연결 실패", "PermissionDeniedError": "키에 권한이 없다",
                "NotFoundError": f"모델 {model_id!r} 을 찾을 수 없다(AT_AI_MODEL 확인)"}.get(name, "API 오류")
        return ParseResult(None, error=f"{hint}: {name}: {e}", model=model_id, provider="anthropic")
    usage = _usage(getattr(resp, "usage", None))
    served = getattr(resp, "model", model_id)
    stop = getattr(resp, "stop_reason", "") or ""
    common = {"model": served, "stop_reason": stop, "usage": usage, "provider": "anthropic"}
    if stop == "refusal":
        det = getattr(resp, "stop_details", None)
        why = f" ({getattr(det, 'category', '')}: {getattr(det, 'explanation', '')})" if det else ""
        return ParseResult(None, error=f"모델이 요청을 거절했다{why}", **common)
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if stop == "max_tokens":
        return ParseResult(None, text=text, error="응답이 잘렸다(max_tokens) — 요청을 짧게", **common)
    return _result(text, **common)


def _parse_openrouter(turns: list[dict], catalog: dict, *, post=None, model_id: str) -> ParseResult:
    key = free_key()
    if not key:
        return ParseResult(None, error=f"{OPENROUTER_KEY} 가 없다 — openrouter.ai 에서 무료 키를 받아 `.env` 에 넣는다",
                           provider="openrouter_free")
    messages = [{"role": "system", "content": SYSTEM}, *build_messages(turns, catalog)]
    try:
        out = openrouter.complete(messages, api_key=key, model=model_id, schema=SCHEMA,
                                  max_tokens=MAX_TOKENS, post=post)
    except openrouter.FreeLLMError as e:
        return ParseResult(None, error=str(e), model=model_id, provider="openrouter_free")
    u = out["usage"] or {}
    usage = {"input": int(u.get("prompt_tokens") or 0), "cache_read": 0, "cache_write": 0,
             "output": int(u.get("completion_tokens") or 0), "cost": out["cost"]}
    return _result(out["text"], model=out["model"], stop_reason="end_turn", usage=usage, provider="openrouter_free")


def parse(turns: list[dict], catalog: dict, *, client=None, post=None, provider: str = "",
          model_id: str | None = None) -> ParseResult:
    """대화 → 명세 dict(검증 전). 오류는 예외 대신 `ParseResult.error` 로(화면이 그대로 보인다).

    `client` 를 주면 anthropic, `post` 를 주면 openrouter 경로로 고정한다(테스트 주입).
    """
    if provider:
        name = resolve_provider(provider)
    elif client is not None:
        name = "anthropic"
    elif post is not None:
        name = "openrouter_free"
    else:
        try:
            name = resolve_provider()
        except ValueError as e:
            return ParseResult(None, error=str(e))
    model_id = model_id or model(name)
    if name == "openrouter_free":
        return _parse_openrouter(turns, catalog, post=post, model_id=model_id)
    return _parse_anthropic(turns, catalog, client=client, model_id=model_id)
