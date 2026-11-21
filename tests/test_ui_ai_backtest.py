"""AI 백테스트 페이지 — 키 없음 안내·JSON 실행·오류 표시·DB 미설정·가짜 LLM 대화. 네트워크 0."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.aibt import llm
from src.shared import settings
from tests import aibt_fixtures as fx

PAGE = str(Path(__file__).resolve().parents[1] / "src" / "ui" / "views" / "ai_backtest.py")


@pytest.fixture
def db(tmp_path, monkeypatch):
    url = fx.etf_like(tmp_path / "etf.db")
    monkeypatch.setenv("AT_MARKET_DB_URL", url)
    monkeypatch.setattr(llm, "available", lambda: False)
    settings.reload()
    return url


def run_page():
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception, at.exception
    return at


def test_no_key_notice_and_editor(db):
    at = run_page()
    assert any("ANTHROPIC_API_KEY" in i.value for i in at.info)
    assert at.text_area(key="ai_spec_editor").value.startswith("{")
    assert at.chat_input(key="ai_prompt").disabled


def test_run_example_spec_renders_result(db):
    at = run_page()
    at = at.button(key="ai_run_spec").click().run()
    assert not at.exception, at.exception
    assert len(at.metric) == 6 and at.metric[1].label == "단위가치 배수"
    assert any("QLD" in s.value for s in at.subheader)
    assert any("벤치마크 QQQ" in c.value for c in at.caption)


def test_invalid_json_and_spec_errors(db):
    at = run_page()
    at = at.text_area(key="ai_spec_editor").input("{bad json").run()
    at = at.button(key="ai_run_spec").click().run()
    assert any("JSON 문법 오류" in e.value for e in at.error)
    bad = at.text_area(key="ai_spec_editor").value.replace("{bad json", '{"rule": "buy_and_hold", "weights": [], "x": 1}')
    at = at.text_area(key="ai_spec_editor").input(bad).run()
    at = at.button(key="ai_run_spec").click().run()
    assert any("명세 오류" in e.value and "모르는 키" in e.value for e in at.error)


def test_without_db_url_shows_setup_notice(monkeypatch):
    monkeypatch.setenv("AT_MARKET_DB_URL", "")
    settings.reload()
    monkeypatch.setattr(settings, "_dotenv", lambda: {})  # 빈 환경변수는 .env 로 넘어가므로 로컬 .env 의 URL 과 격리(reload 뒤에 패치)
    at = run_page()
    assert any("AT_MARKET_DB_URL" in i.value for i in at.info) and not at.metric


def test_chat_with_fake_llm_runs_and_records_usage(db, monkeypatch):
    raw = {"title": "QQQ 단순 보유", "start": "2016-01-01", "end": "", "initial_cash": 10000, "monthly_contribution": 0, "benchmark": "",
           "rule": "buy_and_hold", "weights": [{"symbol": "QQQ", "weight": 1}], "signal_symbol": "", "ma_window": 0, "hold_symbol": "",
           "else_symbol": "", "ref_symbol": "", "stages": [], "vehicle": "", "check": "daily", "reset_years": 0, "reserve_fraction": 0,
           "rebalance_period": "monthly"}
    import json
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "parse", lambda turns, cat, **k: llm.ParseResult(raw, text=json.dumps(raw), model="fake",
                                                                             usage={"input": 10, "cache_read": 0, "cache_write": 5, "output": 3}))
    at = run_page()
    at = at.chat_input(key="ai_prompt").set_value("QQQ 그냥 들고 있기 2016년부터").run()
    assert not at.exception, at.exception
    assert any("QQQ 단순 보유" in s.value for s in at.subheader)
    assert len(at.chat_message) == 2 and at.chat_message[1].markdown[0].value.startswith("**QQQ 단순 보유**")
    assert any("AI 토큰 사용" in e.label for e in at.expander)
    # 검증 실패 명세는 편집기로 내려오고 사유를 보인다
    bad = {**raw, "weights": []}
    monkeypatch.setattr(llm, "parse", lambda turns, cat, **k: llm.ParseResult(bad, text=json.dumps(bad), model="fake", usage={}))
    at = at.chat_input(key="ai_prompt").set_value("비중 빼 줘").run()
    assert not at.exception
    assert "검증을 통과하지 못했다" in at.chat_message[-1].markdown[0].value
