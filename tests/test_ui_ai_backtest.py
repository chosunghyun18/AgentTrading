"""AI 백테스트 페이지(대화창 전용) — 프리셋 직행·AI 번역·오류 안내·세팅 파일 반영. 네트워크 0."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.aibt import llm
from src.shared import settings
from tests import aibt_fixtures as fx

PAGE = str(Path(__file__).resolve().parents[1] / "src" / "ui" / "views" / "ai_backtest.py")
SPEC = {"title": "QQQ 단순 보유", "start": "2016-01-01", "end": "", "initial_cash": 10000, "monthly_contribution": 0,
        "benchmark": "", "rule": "buy_and_hold", "weights": [{"symbol": "QQQ", "weight": 1}], "signal_symbol": "",
        "ma_window": 0, "hold_symbol": "", "else_symbol": "", "ref_symbol": "", "stages": [], "vehicle": "",
        "check": "daily", "reset_years": 0, "reserve_fraction": 0, "rebalance_period": "monthly"}
PRESETS = {"defaults": {"initial_cash": 50000, "benchmark": "QQQ"},
           "preferences": "하락장에 담는다.",
           "presets": {"내 기본": {"rule": "buy_and_hold", "weights": [{"symbol": "QLD", "weight": 1.0}]},
                       "망가진 것": {"rule": "ma_filter"}}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """시세 DB(합성) + 세팅 파일 경로. 기본은 AI 꺼짐."""
    monkeypatch.setenv("AT_MARKET_DB_URL", fx.etf_like(tmp_path / "etf.db"))
    cfg = tmp_path / "aibt.json"
    monkeypatch.setenv("AT_AIBT_CONFIG", str(cfg))
    monkeypatch.setattr(llm, "available", lambda: False)
    settings.reload()
    return cfg


def write_cfg(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data, encoding="utf-8")


def run_page():
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception, at.exception
    return at


def chat_text(at) -> str:
    return "\n".join(b.value for m in at.chat_message for b in m.markdown)


def fake_parse(raw, **kw):
    def _p(turns, cat, **kwargs):
        _p.seen, _p.turns = kwargs, list(turns)
        return llm.ParseResult(raw, text=json.dumps(raw), model="fake",
                               usage={"input": 10, "cache_read": 0, "cache_write": 0, "output": 3, "cost": 0.0}, **kw)
    return _p


# ── 대화창만 있는 화면 ───────────────────────────────────────────────────────────────────────────

def test_chat_only_layout_has_no_settings_widgets(env):
    at = run_page()
    assert at.chat_input and not at.text_area and not at.button  # 명세 편집기·실행 버튼 없음
    assert "AI 꺼짐" in "\n".join(c.value for c in at.caption)


def test_greeting_explains_usage_and_missing_key(env):
    at = run_page()
    t = chat_text(at)
    assert "말로 설명하면" in t and "OPENROUTER_API_KEY" in t and "aibt.example.json" in t


def test_greeting_lists_presets(env):
    write_cfg(env, PRESETS)
    at = run_page()
    assert "내 기본" in chat_text(at) and "프리셋 2개" in "\n".join(c.value for c in at.caption)


# ── 프리셋 직행(AI 없음) ─────────────────────────────────────────────────────────────────────────

def test_preset_name_runs_without_ai(env, monkeypatch):
    write_cfg(env, PRESETS)
    monkeypatch.setattr(llm, "parse", lambda *a, **k: pytest.fail("프리셋은 AI 를 부르면 안 된다"))
    at = run_page()
    at = at.chat_input(key="ai_prompt").set_value("내 기본").run()
    assert not at.exception, at.exception
    assert "AI 호출 없음" in chat_text(at)
    assert len(at.metric) == 6 and at.metric[1].label == "단위가치 배수"


def test_preset_applies_defaults_from_file(env):
    write_cfg(env, PRESETS)
    at = run_page().chat_input(key="ai_prompt").set_value("/내 기본").run()
    assert not at.exception
    # defaults 의 initial_cash 50000 이 쓰였는지 — 결과 카드의 총납입 도움말로 확인
    assert "50,000" in at.metric[0].help


def test_broken_preset_points_at_the_file(env):
    write_cfg(env, PRESETS)
    at = run_page().chat_input(key="ai_prompt").set_value("망가진 것").run()
    assert not at.exception
    t = chat_text(at)
    assert "올바르지 않다" in t and str(env) in t and not at.metric


def test_bad_config_file_warns_but_page_still_works(env):
    write_cfg(env, "{깨진 json")
    at = run_page()
    assert any("세팅 파일을 읽지 못해" in w.value for w in at.warning)
    assert at.chat_input  # 화면은 계속 쓸 수 있다


def test_config_change_is_picked_up(env):
    write_cfg(env, {"presets": {"하나": {"rule": "buy_and_hold", "weights": [{"symbol": "QQQ", "weight": 1}]}}})
    assert "하나" in chat_text(run_page())
    write_cfg(env, {"presets": {"둘": {"rule": "buy_and_hold", "weights": [{"symbol": "QQQ", "weight": 1}]}}})
    t = chat_text(run_page())
    assert "둘" in t and "하나" not in t


# ── AI 경로 ──────────────────────────────────────────────────────────────────────────────────────

def test_natural_language_runs_and_sends_user_settings(env, monkeypatch):
    write_cfg(env, PRESETS)
    monkeypatch.setattr(llm, "available", lambda: True)
    p = fake_parse(SPEC)
    monkeypatch.setattr(llm, "parse", p)
    at = run_page().chat_input(key="ai_prompt").set_value("QQQ 그냥 들고 있기").run()
    assert not at.exception, at.exception
    assert "QQQ 단순 보유" in chat_text(at) and len(at.metric) == 6
    assert "하락장에 담는다" in p.seen["user_block"] and "내 기본" in p.seen["user_block"]
    assert any("AI 토큰 사용" in e.label for e in at.expander)


def test_ai_off_message_names_the_key_and_presets(env):
    write_cfg(env, PRESETS)
    at = run_page().chat_input(key="ai_prompt").set_value("아무 말이나").run()
    assert not at.exception
    t = chat_text(at)
    assert "AI 가 꺼져 있어" in t and "OPENROUTER_API_KEY" in t and "내 기본" in t


def test_invalid_ai_spec_shows_reason_and_json_in_chat(env, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "parse", fake_parse({**SPEC, "weights": []}))
    at = run_page().chat_input(key="ai_prompt").set_value("비중 빼 줘").run()
    assert not at.exception
    t = chat_text(at)
    assert "검증을 통과하지 못했다" in t and "```json" in t and str(env) in t
    assert not at.text_area  # 편집기는 여전히 없다


def test_provider_error_is_shown_in_chat(env, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "parse", lambda *a, **k: llm.ParseResult(None, error="요청 한도에 걸렸다", model="m"))
    at = run_page().chat_input(key="ai_prompt").set_value("뭐든").run()
    assert not at.exception and "요청 한도에 걸렸다" in chat_text(at)


# ── 환경 문제 ────────────────────────────────────────────────────────────────────────────────────

def test_without_db_url_shows_setup_notice(monkeypatch):
    monkeypatch.setenv("AT_MARKET_DB_URL", "")
    settings.reload()
    monkeypatch.setattr(settings, "_dotenv", lambda: {})  # 빈 환경변수는 .env 로 넘어가므로 격리(reload 뒤에 패치)
    at = run_page()
    assert any("AT_MARKET_DB_URL" in i.value for i in at.info) and not at.metric


# ── 독립 검토 반영 ───────────────────────────────────────────────────────────────────────────────

def test_pasted_spec_json_runs_without_ai(env, monkeypatch):
    """키도 프리셋도 없는 사용자의 탈출구 — 명세 JSON 을 대화창에 붙여 넣으면 바로 돈다."""
    monkeypatch.setattr(llm, "parse", lambda *a, **k: pytest.fail("붙여 넣은 명세는 AI 를 부르면 안 된다"))
    at = run_page().chat_input(key="ai_prompt").set_value(json.dumps(SPEC, ensure_ascii=False)).run()
    assert not at.exception, at.exception
    assert "붙여 넣은 명세" in chat_text(at) and len(at.metric) == 6


def test_pasted_spec_with_prose_and_fence(env):
    at = run_page().chat_input(key="ai_prompt").set_value(f"이거 돌려줘\n```json\n{json.dumps(SPEC)}\n```").run()
    assert not at.exception and len(at.metric) == 6


def test_pasted_invalid_spec_reports_reason(env):
    at = run_page().chat_input(key="ai_prompt").set_value(json.dumps({**SPEC, "weights": []})).run()
    assert not at.exception
    assert "검증을 통과하지 못했다" in chat_text(at) and not at.metric


def test_failed_turn_clears_previous_result(env, monkeypatch):
    """오류 문구 아래 옛 차트·KPI 가 남지 않아야 한다."""
    write_cfg(env, PRESETS)
    at = run_page().chat_input(key="ai_prompt").set_value("내 기본").run()
    assert len(at.metric) == 6
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "parse", lambda *a, **k: llm.ParseResult(None, error="요청 한도에 걸렸다"))
    at = at.chat_input(key="ai_prompt").set_value("2015년부터로 바꿔").run()
    assert not at.exception
    assert "요청 한도에 걸렸다" in chat_text(at) and not at.metric  # 이전 결과 사라짐


def test_invalid_ai_answer_stays_in_model_context(env, monkeypatch):
    """검증 실패한 모델 답도 대화 맥락에 남아 다음 턴이 무엇이 틀렸는지 안다."""
    monkeypatch.setattr(llm, "available", lambda: True)
    bad = {**SPEC, "weights": []}
    monkeypatch.setattr(llm, "parse", fake_parse(bad))
    at = run_page().chat_input(key="ai_prompt").set_value("비중 빼 줘").run()
    p2 = fake_parse(SPEC)
    monkeypatch.setattr(llm, "parse", p2)
    at = at.chat_input(key="ai_prompt").set_value("아니 QQQ 100% 로").run()
    assert not at.exception and len(at.metric) == 6
    # 2번째 호출이 받은 대화: 1턴 요청 → 검증 실패한 모델 답 → 2턴 요청
    assert [t["role"] for t in p2.turns] == ["user", "assistant", "user"]
    assert json.loads(p2.turns[1]["content"])["weights"] == []  # 틀린 답이 맥락에 남아 있다
    assert p2.turns[2]["content"] == "아니 QQQ 100% 로"


def test_preset_then_followup_keeps_preset_context(env, monkeypatch):
    write_cfg(env, PRESETS)
    at = run_page().chat_input(key="ai_prompt").set_value("내 기본").run()
    monkeypatch.setattr(llm, "available", lambda: True)
    p = fake_parse(SPEC)
    monkeypatch.setattr(llm, "parse", p)
    at = at.chat_input(key="ai_prompt").set_value("2015년부터로 바꿔").run()
    assert not at.exception and len(at.metric) == 6
    # 프리셋 실행이 맥락이 되어 후속 수정이 그 위에서 이어진다
    assert [t["role"] for t in p.turns] == ["user", "assistant", "user"]
    assert json.loads(p.turns[1]["content"])["weights"] == [{"symbol": "QLD", "weight": 1.0}]


def test_config_path_shown_is_not_absolute_user_path(env):
    """경로에 사용자 이름이 그대로 찍히지 않게 — 저장소 밖이면 절대경로지만 저장소 안이면 상대경로."""
    from src.shared import settings as cfgmod
    at = run_page()
    shown = "\n".join(c.value for c in at.caption)
    assert str(cfgmod.ROOT) not in shown  # tmp 경로는 저장소 밖이라 그대로지만 ROOT 접두는 없어야 한다
