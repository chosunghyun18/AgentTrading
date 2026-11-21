"""LLM 번역 계층 — 공급자 선택(무료·유료), 가짜 전송으로 요청 형태·응답 처리. `anthropic` 을 import 하지 않는다."""

import json
import types
from datetime import date

import pytest

from src.aibt import llm
from src.aibt import spec as S

CAT = {"QQQ": (date(2000, 1, 3), date(2026, 11, 9)), "TQQQ": (date(2010, 2, 11), date(2026, 11, 9))}
RAW = {"title": "t", "start": "", "end": "", "initial_cash": 10000, "monthly_contribution": 0, "benchmark": "QQQ", "rule": "buy_and_hold",
       "weights": [{"symbol": "TQQQ", "weight": 1}], "signal_symbol": "", "ma_window": 0, "hold_symbol": "", "else_symbol": "",
       "ref_symbol": "", "stages": [], "vehicle": "", "check": "daily", "reset_years": 0, "reserve_fraction": 0, "rebalance_period": "monthly"}


def response(text=None, stop="end_turn", model="claude-opus-5-5", details=None):
    usage = types.SimpleNamespace(input_tokens=1200, cache_read_input_tokens=1000, cache_creation_input_tokens=0, output_tokens=90)
    content = [types.SimpleNamespace(type="text", text=text if text is not None else json.dumps(RAW))]
    return types.SimpleNamespace(content=content, stop_reason=stop, model=model, usage=usage, stop_details=details)


class Fake:
    def __init__(self, resp=None, raise_=None):
        self.calls = []
        self.resp = resp or response()
        self.raise_ = raise_
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.raise_:
            raise self.raise_
        return self.resp


def test_request_shape_and_result():
    fake = Fake()
    turns = [{"role": "user", "content": "TQQQ 그냥 들고 있기"}]
    pr = llm.parse(turns, CAT, client=fake)
    assert pr.error == "" and pr.raw == RAW and pr.model == "claude-opus-5-5"
    assert pr.usage == {"input": 1200, "cache_read": 1000, "cache_write": 0, "output": 90}
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == llm.MAX_TOKENS
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"} and kw["system"][0]["text"] == llm.SYSTEM
    assert kw["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": S.SCHEMA}}
    assert kw["betas"] == llm.BETAS and kw["fallbacks"] == "default"
    assert kw["messages"][0]["content"].startswith("사용 가능한 종목") and kw["messages"][0]["content"].endswith("요청: TQQQ 그냥 들고 있기")
    assert "TQQQ: 2010-02-11" in kw["messages"][0]["content"]
    assert S.validate(pr.raw, CAT).weights == {"TQQQ": 1.0}


def test_multi_turn_keeps_history_and_catalog_once():
    fake = Fake()
    turns = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "{...}"}, {"role": "user", "content": "2015년부터"}]
    llm.parse(turns, CAT, client=fake)
    msgs = fake.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert "사용 가능한 종목" in msgs[0]["content"] and msgs[2]["content"] == "2015년부터"


def test_model_override_from_settings(monkeypatch):
    monkeypatch.setenv("AT_AI_MODEL", "claude-haiku-5-5")
    fake = Fake()
    llm.parse([{"role": "user", "content": "x"}], CAT, client=fake)
    assert fake.calls[0]["model"] == "claude-haiku-5-5"


def test_refusal_and_truncation_and_bad_json():
    det = types.SimpleNamespace(category="general_harms", explanation="no")
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, client=Fake(response(stop="refusal", details=det)))
    assert pr.raw is None and "거절" in pr.error and "general_harms" in pr.error
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, client=Fake(response(text='{"a":', stop="max_tokens")))
    assert pr.raw is None and "잘렸다" in pr.error
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, client=Fake(response(text="not json")))
    assert pr.raw is None and "JSON" in pr.error and pr.text == "not json"
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, client=Fake(response(text="[1,2]")))
    assert pr.raw is None and "찾지 못했다" in pr.error  # 객체가 아니면 추출 실패와 같은 취급


def test_client_exceptions_are_reported_not_raised():
    class RateLimitError(Exception):
        pass
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, client=Fake(raise_=RateLimitError("429")))
    assert pr.raw is None and "요청 한도" in pr.error and "RateLimitError" in pr.error

    class Weird(Exception):
        pass
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, client=Fake(raise_=Weird("boom")))
    assert "API 오류" in pr.error and "boom" in pr.error


def test_api_key_resolution(monkeypatch):
    assert llm.api_key() == "" and llm.free_key() == "" and llm.available() is False
    monkeypatch.setattr(llm, "dotenv_values", lambda *a, **k: {"ANTHROPIC_API_KEY": "sk-from-dotenv"})
    assert llm.api_key() == "sk-from-dotenv"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert llm.api_key() == "sk-env"


def test_sdk_missing_is_reported(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "anthropic":
            raise ImportError("no sdk")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, provider="anthropic")
    assert pr.raw is None and "SDK" in pr.error
    assert llm.sdk_available() is False


# ── 응답에서 JSON 꺼내기(무료 모델은 형식을 덜 지킨다) ────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('설명입니다.\n\n```json\n{"a": 1}\n```\n\n끝.', {"a": 1}),
    ('여기 있습니다: {"a": {"b": 2}} 이상입니다', {"a": {"b": 2}}),
    ('{"t": "중괄호 } 포함", "u": "\\"따옴표\\""}', {"t": "중괄호 } 포함", "u": '"따옴표"'}),
])
def test_extract_json_tolerates_messy_output(text, want):
    assert llm.extract_json(text) == want


@pytest.mark.parametrize("text", ["", "   ", "설명만 있고 JSON 은 없다", "[1, 2]", '{"a": ', "```json\n{\n```"])
def test_extract_json_gives_up_cleanly(text):
    assert llm.extract_json(text) is None


# ── 공급자 선택 ──────────────────────────────────────────────────────────────────────────────────

def test_auto_prefers_free_then_paid(monkeypatch):
    assert llm.resolve_provider() == "openrouter_free"      # 키가 없으면 무료가 기본값
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
    assert llm.resolve_provider() == "anthropic"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    assert llm.resolve_provider() == "openrouter_free"      # 둘 다 있으면 무료 먼저
    assert llm.available() is True


@pytest.mark.parametrize("value,want", [("openrouter_free", "openrouter_free"), ("anthropic", "anthropic"),
                                        ("  ANTHROPIC  ", "anthropic")])
def test_explicit_provider_setting(monkeypatch, value, want):
    monkeypatch.setenv("AT_AI_PROVIDER", value)
    assert llm.resolve_provider() == want


def test_unknown_provider_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("AT_AI_PROVIDER", "ollama")
    pr = llm.parse([{"role": "user", "content": "x"}], CAT)
    assert pr.raw is None and "AT_AI_PROVIDER" in pr.error


def test_model_per_provider(monkeypatch):
    monkeypatch.setenv("AT_AI_FREE_MODEL", "meta-llama/llama-3.2-3b-instruct:free")
    assert llm.model("openrouter_free") == "meta-llama/llama-3.2-3b-instruct:free"
    assert llm.model("anthropic") == "claude-opus-5-5"


def test_status_reports_missing_keys_without_values(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    s = llm.status()
    assert s["provider"] == "openrouter_free" and s["ready"] is True and s["paid_ready"] is False
    assert "sk-or-secret" not in repr(s) and s["free_key"] == "OPENROUTER_API_KEY"


# ── 무료 경로 ────────────────────────────────────────────────────────────────────────────────────

def free_post(status=200, text=None, cost=0):
    import json as _j
    payload = _j.dumps({"choices": [{"message": {"content": text if text is not None else _j.dumps(RAW)},
                                     "finish_reason": "stop"}],
                        "model": "deepseek/deepseek-r1:free",
                        "usage": {"prompt_tokens": 800, "completion_tokens": 120, "cost": cost}})
    calls = []

    def post(url, *, headers, body, timeout):
        calls.append({"url": url, "headers": headers, "body": body})
        return status, payload
    post.calls = calls
    return post


def test_free_path_builds_system_message_and_maps_usage(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    post = free_post()
    pr = llm.parse([{"role": "user", "content": "TQQQ 들고 있기"}], CAT, post=post)
    assert pr.error == "" and pr.raw == RAW and pr.provider == "openrouter_free"
    assert pr.model == "deepseek/deepseek-r1:free"
    assert pr.usage == {"input": 800, "cache_read": 0, "cache_write": 0, "output": 120, "cost": 0.0}
    msgs = post.calls[0]["body"]["messages"]
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == llm.SYSTEM
    assert "사용 가능한 종목" in msgs[1]["content"] and msgs[1]["content"].endswith("요청: TQQQ 들고 있기")
    assert post.calls[0]["body"]["model"] == "openrouter/free"


def test_free_path_extracts_json_from_fenced_answer(monkeypatch):
    import json as _j
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    pr = llm.parse([{"role": "user", "content": "x"}], CAT,
                   post=free_post(text=f"요청대로 만들었습니다.\n```json\n{_j.dumps(RAW)}\n```"))
    assert pr.raw == RAW and pr.error == ""


def test_free_path_reports_unparseable_answer(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, post=free_post(text="죄송하지만 잘 모르겠습니다."))
    assert pr.raw is None and "찾지 못했다" in pr.error and pr.text == "죄송하지만 잘 모르겠습니다."


def test_free_path_surfaces_cost_violation(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, post=free_post(cost=0.002))
    assert pr.raw is None and "계약 위반" in pr.error


def test_free_path_without_key_explains_how_to_get_one():
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, provider="openrouter_free")
    assert pr.raw is None and "OPENROUTER_API_KEY" in pr.error and "openrouter.ai" in pr.error
