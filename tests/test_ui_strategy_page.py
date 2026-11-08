"""전략 페이지 AppTest — 현재 적용 전략(없음), 관문 진행표, 전략 카드, v1 워크포워드 선택 문장, 버튼 없음."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.agent import service, settings
from src.agent import strategies as sg
from src.agent.service import TradingService
from src.ui import strategy_view as sv
from tests import ui_fixtures as fx
from tests.agent_fakes import FakeClient

PAGE = Path(__file__).resolve().parents[1] / "src" / "ui" / "views" / "strategy.py"


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    return fx.build(tmp_path_factory.mktemp("ui"))


@pytest.fixture
def env(tree, tmp_path, monkeypatch):
    monkeypatch.setenv("AT_OUT_DIR", str(tree["out"]))
    monkeypatch.setenv("AT_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setattr(settings, "load_env", lambda: None)
    monkeypatch.setattr(service, "FACTORY",
                        lambda mode: TradingService(FakeClient(), mode, tmp_path / "agent", tmp_path / "r.yaml"))
    # 픽스처 리포트 이름으로 v1 결과를 연결
    reg = tuple(s if s.id != "v1" else sg.Strategy(**{**s.__dict__, "report": fx.REPORT}) for s in sg.REGISTRY)
    monkeypatch.setattr(sg, "REGISTRY", reg)
    monkeypatch.setattr(sg, "registry", lambda: reg)
    return tree


def _md(at) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_strategy_page(env):
    at = AppTest.from_file(str(PAGE), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    md = _md(at)
    assert "에이전트 미가동" in md and "없음" in md
    assert md.count('class="pipe-row"') == len(sg.REGISTRY)
    for s in sg.REGISTRY:
        assert s.name in md
    assert md.count('class="kpi-card') == 4  # v1 워크포워드 결과
    assert "진입: " in md and ("(모멘텀)" in md or "(돌파)" in md)  # 선택 규칙 문장
    assert len(at.button) == 0  # 읽기 전용
    assert any("다음 후보 없음" in c.value for c in at.caption)  # 2026-10-10 C1 시도 2 fail


def test_pipeline_html_states():
    h = sv.pipeline_html(sg.REGISTRY)
    assert h.count("<tr") == len(sg.REGISTRY) + 1
    assert "미달" in h and "완료" in h
    assert "phase3-next" not in h  # 문서 경로가 아니라 사유만
    assert 'title="Sharpe·DSR 미달"' in h


def test_pipeline_links_to_gate_page():
    h = sv.pipeline_html(sg.REGISTRY)
    assert h.count('target="_self"') == len(sg.STEPS) * (len(sg.REGISTRY) + 1)  # 머리글 + 칸 전부
    assert 'href="gates?gate=wf"' in h and 'href="gates?gate=oos&amp;strategy=v1"' in h


def test_gates_cover_every_step():
    assert set(sv.GATES) == set(sg.STEPS) and set(sg.STEP_KEYS) == set(sg.STEPS)
    assert len(set(sg.STEP_KEYS.values())) == len(sg.STEPS)
    for g in sv.GATES.values():
        assert g.alias and g.plain and g.criteria and g.exact and g.terms and g.docs
        assert all(isinstance(t, tuple) for t in (g.criteria, g.exact, g.terms, g.docs))
        assert set(g.terms) <= set(sv.TERMS)  # 용어 풀이가 빠진 용어 없음
    assert sv.gate_by_key("paper") == "페이퍼 8주" and sv.gate_by_key("nope") == sv.gate_by_key(None) == "설계"


GATES_PAGE = PAGE.parent / "gates.py"


@pytest.mark.parametrize("key", ["wf", "live", "bogus"])
def test_gates_page_opens_requested_gate(env, key):
    at = AppTest.from_file(str(GATES_PAGE), default_timeout=30)
    at.query_params["gate"] = key
    at.query_params["strategy"] = "C1"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    step = sv.gate_by_key(key)
    md = _md(at)
    assert any(h.value.endswith(f"{step} — {sv.GATES[step].alias}") for h in at.subheader)
    assert sv.GATES[step].criteria[0] in md
    assert md.count('class="pipe-row') == len(sg.REGISTRY) and md.count("pipe-row gate-row-hit") == 1
    assert sv.GATES[step].plain in md and md.count('class="term-row"') == len(sv.GATES[step].terms)
    assert len(at.button) == 0  # 읽기 전용


def test_gates_page_switch_updates_url(env):
    at = AppTest.from_file(str(GATES_PAGE), default_timeout=30).run()
    at.button_group(key="gate-pick").set_value("OOS 1회").run()
    assert not at.exception
    assert at.query_params["gate"] in ("oos", ["oos"])
    assert any(h.value.endswith(sv.GATES["OOS 1회"].alias) for h in at.subheader)


def test_gates_page_switch_clears_highlight_and_deselect_keeps_gate(env):
    at = AppTest.from_file(str(GATES_PAGE), default_timeout=30)
    at.query_params["gate"] = "wf"
    at.query_params["strategy"] = "C1"
    at.run()
    at.button_group(key="gate-pick").set_value("OOS 1회").run()
    assert "strategy" not in at.query_params and _md(at).count("pipe-row gate-row-hit") == 0
    at.button_group(key="gate-pick").set_value(None).run()  # 같은 항목 다시 누름 = 선택 해제
    assert not at.exception
    assert at.button_group(key="gate-pick").value == "OOS 1회"
