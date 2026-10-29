"""W3 최소 — 에이전트 화면: 빈 안내, 목록·run·자본곡선, 정지 버튼 → commands."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.shared import settings
from src.store import db
from src.strategy import agent

PAGE = str(Path(__file__).resolve().parents[1] / "src" / "ui" / "views" / "agents.py")


@pytest.fixture
def store(tmp_path, monkeypatch):
    p = tmp_path / "s.sqlite"
    monkeypatch.setenv("AT_STORE_PATH", str(p))
    settings.reload()
    return p


def test_empty_notice(store):
    at = AppTest.from_file(PAGE, default_timeout=30).run()
    assert not at.exception
    assert "등록된 에이전트가 없다" in at.info[0].value


def test_agents_runs_and_stop(store):
    con = db.connect(store)
    db.add_agent(con, agent.from_mapping({"id": "s1", "strategy": "sma_cross", "mode": "shadow"}))
    run = db.start_run(con, "s1", "shadow")
    db.write_equity(con, run, "2026-10-29T00:00:00+00:00", 1000)
    db.write_equity(con, run, "2026-10-29T00:01:00+00:00", 1001)
    db.add_event(con, run, "2026-10-29T00:01:00+00:00", "signal", side="long")
    at = AppTest.from_file(PAGE, default_timeout=30).run()
    assert not at.exception
    assert "실행 중" in at.warning[0].value
    at.button(key="stop").click().run()
    assert not at.exception
    assert db.take_commands(con, "s1") == ["stop"]
