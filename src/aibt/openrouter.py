"""OpenRouter **무료 모델 전용** 호출 — 돈이 나가는 경로를 구조적으로 막는다.

계약은 autodev `free_llm.py` 와 같다(단일 소스: 볼트
`Projects/work/autodev/무료 LLM API 오픈 소스 조사와 로컬 프로젝트 공통 적용.md`, 2026-10-17 결정):

- 공식 OpenRouter HTTPS 주소만. 로컬·Ollama·다른 호스트는 거부한다.
- 모델은 `openrouter/free`(무료 라우터) 또는 명시적 `제공자/모델:free` 만.
- `provider.allow_fallbacks=false` + `provider.max_price` prompt·completion **0** — 유료 모델로 새지 않는다.
- 응답 `usage.cost` 가 0 이 아니면 **거부**한다(계약 위반). 비용이 찍힌 호출은 결과를 쓰지 않는다.
- 리다이렉트를 따라가지 않고, 응답 크기에 상한을 두고, 429·5xx 만 제한적으로 재시도한다.
- 키는 환경변수 이름으로만 다루고 값은 로그·예외 문구에 넣지 않는다.

새 의존성 없음(`requests` 는 핵심 의존성). 테스트는 `post=` 로 전송을 주입한다.
"""

from __future__ import annotations

import json
import math
import re
import time

BASE_URL = "https://openrouter.ai/api/v1"
CHAT_URL = f"{BASE_URL}/chat/completions"
KEY_NAME = "OPENROUTER_API_KEY"
FREE_ROUTER = "openrouter/free"
FREE_MODEL_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:free")
MAX_BYTES = 2_000_000
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
# 조건에 맞는 무료 모델이 없을 때 OpenRouter 가 내는 상태. 스키마를 빼고 한 번만 다시 시도한다.
NO_ENDPOINT_STATUS = frozenset({400, 404})


class FreeLLMError(ValueError):
    """무료 호출 계약 위반·설정 오류·공급자 실패. 메시지에 키 값은 넣지 않는다."""


def check_model(model: str) -> str:
    if not isinstance(model, str):
        raise FreeLLMError("모델 이름이 문자열이 아니다")
    m = model.strip()
    if m != FREE_ROUTER and not FREE_MODEL_RE.fullmatch(m):
        raise FreeLLMError(f"무료 모델만 허용한다 — {FREE_ROUTER!r} 또는 '제공자/모델:free' 형식: {model!r}")
    return m


def _default_post(url: str, *, headers: dict, body: dict, timeout: float) -> tuple[int, str]:
    """실제 전송. 리다이렉트 금지 + 읽기 상한."""
    import requests

    r = requests.post(url, headers=headers, json=body, timeout=timeout, allow_redirects=False, stream=True)
    try:
        raw = r.raw.read(MAX_BYTES + 1, decode_content=True) or b""
        if len(raw) > MAX_BYTES:
            raise FreeLLMError("응답 크기 상한을 넘었다")
        return r.status_code, raw.decode("utf-8", "replace")
    finally:
        r.close()


def _payload(model: str, messages: list[dict], max_tokens: int, schema: dict | None) -> dict:
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
        # 유료 전환을 막는 두 장치: 폴백 금지 + 가격 상한 0
        "provider": {"allow_fallbacks": False, "max_price": {"prompt": 0, "completion": 0}},
        "usage": {"include": True},  # cost 를 받아 0 인지 확인한다
    }
    if schema is not None:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "backtest_spec", "strict": True, "schema": schema}}
    return body


def _read(text: str, model: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise FreeLLMError("공급자 응답이 JSON 이 아니다") from None
    if not isinstance(data, dict):
        raise FreeLLMError("공급자 응답 형식이 올바르지 않다")
    if data.get("error"):
        msg = (data["error"] or {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
        raise FreeLLMError(f"공급자 오류: {str(msg)[:200]}")
    try:
        choice = data["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError):
        raise FreeLLMError("공급자 응답에 결과가 없다") from None
    if message.get("refusal"):
        raise FreeLLMError("모델이 요청을 거절했다")
    content = message.get("content")
    finish = choice.get("finish_reason")
    if not isinstance(content, str) or not content.strip():
        raise FreeLLMError("빈 응답을 받았다")
    if finish not in (None, "stop"):
        raise FreeLLMError(f"응답이 끝까지 생성되지 않았다(finish_reason={finish})")
    usage = data.get("usage") or {}
    cost = usage.get("cost", 0) or 0
    try:
        cost = float(cost)
    except (TypeError, ValueError):
        raise FreeLLMError("비용 값을 해석할 수 없다") from None
    if not math.isfinite(cost) or cost != 0:
        raise FreeLLMError(f"무료 호출 계약 위반 — 비용 {cost} 이 청구됐다. 결과를 쓰지 않는다")
    return {"text": content, "model": data.get("model") or model, "usage": usage, "cost": cost}


def complete(messages: list[dict], *, api_key: str, model: str = FREE_ROUTER, schema: dict | None = None,
             max_tokens: int = 2048, timeout: float = 60.0, retries: int = 2,
             post=None, sleep=time.sleep) -> dict:
    """무료 모델로 1회 완료. 실패는 `FreeLLMError`. 반환 `{text, model, usage, cost, used_schema}`."""
    model = check_model(model)
    key = (api_key or "").strip()
    if not key:
        raise FreeLLMError(f"{KEY_NAME} 가 비어 있다")
    if not messages:
        raise FreeLLMError("보낼 메시지가 없다")
    if not 1 <= max_tokens <= 8192:
        raise FreeLLMError(f"max_tokens 범위는 1~8192: {max_tokens}")
    if not 0 <= retries <= 2:
        raise FreeLLMError(f"retries 범위는 0~2: {retries}")
    send = post or _default_post
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "AgentTrading"}
    use_schema = schema
    attempt = 0
    while True:
        status, text = send(CHAT_URL, headers=headers, body=_payload(model, messages, max_tokens, use_schema),
                            timeout=timeout)
        if status == 200:
            out = _read(text, model)
            out["used_schema"] = use_schema is not None
            return out
        if use_schema is not None and status in NO_ENDPOINT_STATUS:
            # 구조화 출력을 지원하는 무료 모델이 없을 수 있다 → 스키마 없이 한 번만 다시(프롬프트가 형식을 설명한다)
            use_schema = None
            continue
        if status in RETRY_STATUS and attempt < retries:
            sleep(min(2 ** attempt, 4))
            attempt += 1
            continue
        hint = {401: "API 키가 거부됐다", 402: "무료 한도를 넘었거나 크레딧이 필요하다", 429: "요청 한도에 걸렸다 — 잠시 뒤 다시",
                404: "조건에 맞는 무료 모델을 찾지 못했다"}.get(status, "공급자 오류")
        raise FreeLLMError(f"OpenRouter HTTP {status}: {hint}. 유료·로컬 폴백 없이 중단한다")
