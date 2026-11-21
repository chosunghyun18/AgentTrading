"""자연어 → 명세 JSON 번역(Claude structured output). AI 는 번역만 하고 계산하지 않는다.

- SDK(`anthropic`, optional extra `ai`)는 **지연 import**. `parse(..., client=...)` 로 클라이언트를 주입하면 SDK 없이 테스트한다.
- 시스템 프롬프트는 고정 문자열 + `cache_control`(캐시). 종목 카탈로그(날짜가 매일 바뀜)는 첫 사용자 턴에 넣어 캐시를 깨지 않는다.
- 응답은 JSON 한 덩어리(`output_config.format` = `spec.SCHEMA`). 해설 없음. `effort: low`.
- 키: `ANTHROPIC_API_KEY` — 환경변수 → `.env`(주입 없이 읽기). 화면 입력 없음.
설계: Obsidian `Projects/work/AgentTrading/task/ai-backtest.md`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from dotenv import dotenv_values

from src.aibt.spec import SCHEMA
from src.shared import settings

KEY_NAME = "ANTHROPIC_API_KEY"
MAX_TOKENS = 4096
BETAS = ["server-side-fallback-2026-07-01"]

SYSTEM = """당신은 ETF 백테스트 **명세 번역기**다. 사용자의 요청(한국어·영어)을 아래 규칙 중 하나의 명세 JSON 으로 바꾼다.
계산·예측·해설은 하지 않는다. JSON 만 낸다.

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
- weights 의 합은 1. 언급 없으면 균등.
- initial_cash 언급 없으면 10000. monthly_contribution 없으면 0(dca 는 반드시 > 0). 통화 단위는 신경 쓰지 않는다.
- start·end 는 YYYY-MM-DD. "2015년부터" → "2015-01-01". 언급 없으면 "". 데이터 범위 밖이면 그대로 두어도 된다(엔진이 자른다).
- 종목은 카탈로그에 있는 코드만 쓴다. 카탈로그: QQQ 나스닥100 1배, QLD 2배, TQQQ 3배, XLK S&P 기술섹터 1배, TECL 기술섹터 3배.
  "나스닥 3배" = TQQQ, "기술주 3배" = TECL. 카탈로그에 없는 종목을 요구하면 가장 가까운 것으로 바꾸고 title 에 적는다.
- benchmark 는 비교할 단순 보유 종목 1개. 사용자가 비교 대상을 말하지 않으면 ""(단, 레버리지 ETF 전략이면 그 기초 1배 종목을 넣는다).
- title 은 한국어 한 줄.
- 이전 명세가 대화에 있으면 그것을 기준으로 사용자가 바꾸라고 한 부분만 바꾼다."""


def api_key() -> str:
    """환경변수 → `.env`. 비면 ""."""
    v = os.environ.get(KEY_NAME, "").strip()
    if v:
        return v
    try:
        return (dotenv_values(settings.ENV_FILE).get(KEY_NAME) or "").strip()
    except OSError:
        return ""


def sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def available() -> bool:
    return bool(api_key()) and sdk_available()


def model() -> str:
    return settings.get("AT_AI_MODEL")


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


@dataclass
class ParseResult:
    raw: dict | None
    text: str = ""
    error: str = ""
    model: str = ""
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=api_key())


def _usage(u) -> dict:
    g = lambda k: int(getattr(u, k, 0) or 0)  # noqa: E731
    return {"input": g("input_tokens"), "cache_read": g("cache_read_input_tokens"),
            "cache_write": g("cache_creation_input_tokens"), "output": g("output_tokens")}


def parse(turns: list[dict], catalog: dict, *, client=None, model_id: str | None = None) -> ParseResult:
    """대화 → 명세 dict(검증 전). 오류는 예외 대신 `ParseResult.error` 로(화면이 그대로 보인다)."""
    model_id = model_id or model()
    try:
        client = client or _client()
    except ImportError:
        return ParseResult(None, error="anthropic SDK 가 설치되지 않았다 — `uv sync --extra ai`")
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
        return ParseResult(None, error=f"{hint}: {name}: {e}", model=model_id)
    usage = _usage(getattr(resp, "usage", None))
    served = getattr(resp, "model", model_id)
    stop = getattr(resp, "stop_reason", "") or ""
    if stop == "refusal":
        det = getattr(resp, "stop_details", None)
        why = f" ({getattr(det, 'category', '')}: {getattr(det, 'explanation', '')})" if det else ""
        return ParseResult(None, error=f"모델이 요청을 거절했다{why}", model=served, stop_reason=stop, usage=usage)
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if stop == "max_tokens":
        return ParseResult(None, text=text, error="응답이 잘렸다(max_tokens) — 요청을 짧게", model=served, stop_reason=stop, usage=usage)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        return ParseResult(None, text=text, error=f"JSON 해석 실패: {e}", model=served, stop_reason=stop, usage=usage)
    if not isinstance(raw, dict):
        return ParseResult(None, text=text, error="명세는 JSON 객체여야 한다", model=served, stop_reason=stop, usage=usage)
    return ParseResult(raw, text=text, model=served, stop_reason=stop, usage=usage)
