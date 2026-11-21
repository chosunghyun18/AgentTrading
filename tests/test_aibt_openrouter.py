"""OpenRouter 무료 전용 클라이언트 — 유료로 새지 않는지, 공급자 응답을 어떻게 거르는지. 네트워크 0.

계약 출처: 볼트 `Projects/work/autodev/무료 LLM API 오픈 소스 조사와 로컬 프로젝트 공통 적용.md`(2026-10-17).
"""

import json

import pytest

from src.aibt import openrouter as orr

KEY = "sk-or-secret-value"
MSGS = [{"role": "user", "content": "안녕"}]


def body(text="{}", *, cost=0, finish="stop", refusal=None, model="x/y:free", usage=None):
    msg = {"content": text}
    if refusal is not None:
        msg["refusal"] = refusal
    u = {"prompt_tokens": 10, "completion_tokens": 5, "cost": cost} if usage is None else usage
    return json.dumps({"choices": [{"message": msg, "finish_reason": finish}], "model": model, "usage": u})


class Post:
    """(status, text) 를 차례로 돌려주며 호출을 기록한다."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, *, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def call(*responses, **kw):
    p = Post(*responses)
    kw.setdefault("sleep", lambda _s: None)
    return orr.complete(MSGS, api_key=KEY, post=p, **kw), p


# ── 모델 제한 ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("m", ["openrouter/free", "meta-llama/llama-3.2-3b-instruct:free", "a_b/c.d-1:free"])
def test_accepts_free_models(m):
    assert orr.check_model(f" {m} ") == m


@pytest.mark.parametrize("m", ["gpt-4", "anthropic/claude-opus-5-5", "openrouter/paid", "x/y:paid", "x/y", "", 5, None])
def test_rejects_non_free_models(m):
    with pytest.raises(orr.FreeLLMError):
        orr.check_model(m)


def test_complete_rejects_paid_model_before_sending():
    p = Post((200, body()))
    with pytest.raises(orr.FreeLLMError, match="무료 모델만"):
        orr.complete(MSGS, api_key=KEY, model="anthropic/claude-opus-5-5", post=p)
    assert p.calls == []


# ── 요청 모양 ────────────────────────────────────────────────────────────────────────────────────

def test_payload_pins_host_and_forbids_paid_routes():
    _, p = call((200, body()))
    c = p.calls[0]
    assert c["url"] == "https://openrouter.ai/api/v1/chat/completions" and c["url"].startswith(orr.BASE_URL)
    assert c["body"]["provider"] == {"allow_fallbacks": False, "max_price": {"prompt": 0, "completion": 0}}
    assert c["body"]["model"] == "openrouter/free" and c["body"]["stream"] is False
    assert c["body"]["usage"] == {"include": True}
    assert c["headers"]["Authorization"] == f"Bearer {KEY}"


def test_schema_is_sent_as_strict_json_schema():
    schema = {"type": "object", "properties": {}}
    _, p = call((200, body()), schema=schema)
    rf = p.calls[0]["body"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True and rf["json_schema"]["schema"] is schema


def test_default_post_disables_redirects_and_streams(monkeypatch):
    seen = {}

    class R:
        status_code = 200
        raw = type("Raw", (), {"read": staticmethod(lambda n, decode_content=False: b'{"ok":1}')})()

        def close(self):
            seen["closed"] = True

    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: seen.update(kw) or R())
    status, text = orr._default_post("https://openrouter.ai/api/v1/chat/completions",
                                     headers={}, body={}, timeout=5)
    assert status == 200 and text == '{"ok":1}'
    assert seen["allow_redirects"] is False and seen["stream"] is True and seen["timeout"] == 5 and seen["closed"]


# ── 비용 계약 ────────────────────────────────────────────────────────────────────────────────────

def test_zero_cost_is_accepted_and_reported():
    out, _ = call((200, body("hello", cost=0)))
    assert out["text"] == "hello" and out["cost"] == 0.0 and out["model"] == "x/y:free" and out["used_schema"] is False


def test_missing_cost_counts_as_zero():
    out, _ = call((200, body("hi", usage={"prompt_tokens": 1})))
    assert out["cost"] == 0.0


@pytest.mark.parametrize("cost", [0.0001, 1, -0.5, float("nan"), float("inf")])
def test_nonzero_cost_is_rejected(cost):
    with pytest.raises(orr.FreeLLMError, match="계약 위반|비용"):
        call((200, body("hello", cost=cost)))


# ── 응답 거르기 ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,msg", [
    (body("", cost=0), "빈 응답"),
    (body("x", finish="length"), "끝까지"),
    (body("x", refusal="no"), "거절"),
    (json.dumps({"error": {"message": "bad key"}}), "공급자 오류"),
    (json.dumps({"choices": []}), "결과가 없다"),
    ("not json at all", "JSON"),
    (json.dumps([1, 2]), "형식"),
])
def test_bad_responses(text, msg):
    with pytest.raises(orr.FreeLLMError, match=msg):
        call((200, text))


# ── 재시도·상태 코드 ─────────────────────────────────────────────────────────────────────────────

def test_retries_429_then_succeeds():
    out, p = call((429, ""), (429, ""), (200, body("ok")))
    assert out["text"] == "ok" and len(p.calls) == 3


def test_retry_limit_then_fails():
    with pytest.raises(orr.FreeLLMError, match="429"):
        call((429, ""), retries=1)


def test_auth_error_is_not_retried():
    p = Post((401, ""))
    with pytest.raises(orr.FreeLLMError, match="키가 거부"):
        orr.complete(MSGS, api_key=KEY, post=p)
    assert len(p.calls) == 1


def test_no_free_endpoint_retries_without_schema():
    out, p = call((404, ""), (200, body("ok")), schema={"type": "object"})
    assert out["used_schema"] is False and len(p.calls) == 2
    assert "response_format" in p.calls[0]["body"] and "response_format" not in p.calls[1]["body"]


def test_no_free_endpoint_without_schema_fails_once():
    p = Post((404, ""))
    with pytest.raises(orr.FreeLLMError, match="무료 모델을 찾지 못했다"):
        orr.complete(MSGS, api_key=KEY, post=p)
    assert len(p.calls) == 1


# ── 입력 검증·비밀 ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kw,msg", [({"api_key": "  "}, "비어"), ({"max_tokens": 0}, "max_tokens"),
                                    ({"max_tokens": 99999}, "max_tokens"), ({"retries": 3}, "retries")])
def test_input_validation(kw, msg):
    with pytest.raises(orr.FreeLLMError, match=msg):
        orr.complete(MSGS, **{"api_key": KEY, "post": Post((200, body())), **kw})


def test_empty_messages_rejected():
    with pytest.raises(orr.FreeLLMError, match="메시지"):
        orr.complete([], api_key=KEY, post=Post((200, body())))


@pytest.mark.parametrize("responses", [[(401, "")], [(200, body(cost=1))], [(200, "nope")]])
def test_key_never_leaks_into_errors(responses):
    with pytest.raises(orr.FreeLLMError) as ei:
        call(*responses)
    assert KEY not in str(ei.value)
