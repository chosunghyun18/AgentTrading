"""LLM 번역 계층 — 가짜 클라이언트로 요청 형태·응답 처리. `anthropic` 을 import 하지 않는다."""

import json
import types
from datetime import date

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
    assert pr.raw is None and "객체" in pr.error


def test_client_exceptions_are_reported_not_raised():
    class RateLimitError(Exception):
        pass
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, client=Fake(raise_=RateLimitError("429")))
    assert pr.raw is None and "요청 한도" in pr.error and "RateLimitError" in pr.error

    class Weird(Exception):
        pass
    pr = llm.parse([{"role": "user", "content": "x"}], CAT, client=Fake(raise_=Weird("boom")))
    assert "API 오류" in pr.error and "boom" in pr.error


def test_api_key_resolution(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm.settings, "ENV_FILE", tmp_path / ".env")
    assert llm.api_key() == "" and llm.available() is False
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-from-dotenv\n", encoding="utf-8")
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
    pr = llm.parse([{"role": "user", "content": "x"}], CAT)
    assert pr.raw is None and "SDK" in pr.error
    assert llm.sdk_available() is False
