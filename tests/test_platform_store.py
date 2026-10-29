"""W1 최소 — SQLite 저장소."""

import sqlite3

import pytest

from src.store import db
from src.strategy import agent


def cfg(id_="a1", mode="shadow", symbol="BTCUSDT"):
    return agent.from_mapping({"id": id_, "strategy": "sma_cross", "mode": mode, "symbol": symbol})


@pytest.fixture
def con(tmp_path):
    c = db.connect(tmp_path / "s.sqlite")
    yield c
    c.close()


def test_schema_and_wal(con):
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("PRAGMA user_version").fetchone()[0] == db.VERSION
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"agents", "trials", "runs", "equity", "events", "commands"} <= tables


def test_newer_store_version_refused(tmp_path):
    p = tmp_path / "s.sqlite"
    db.connect(p).execute(f"PRAGMA user_version={db.VERSION + 1}")
    with pytest.raises(RuntimeError):
        db.connect(p)


def test_agents_unique_and_paper_per_symbol(con):
    db.add_agent(con, cfg())
    with pytest.raises(ValueError, match="이미"):
        db.add_agent(con, cfg())
    db.add_agent(con, cfg("p1", "paper"))
    with pytest.raises(ValueError, match="심볼당"):
        db.add_agent(con, cfg("p2", "paper"))
    db.add_agent(con, cfg("p3", "paper", "ETHUSDT"))
    db.add_agent(con, cfg("s2", "shadow"))  # shadow 는 제한 없음
    assert [a["id"] for a in db.list_agents(con)] == ["a1", "p1", "p3", "s2"]
    assert db.get_agent(con, "p1")["mode"] == "paper"


def test_trials_unique(con):
    a = db.upsert_trial(con, "sma_cross@1", "h", {}, "XBTUSD:x", "default")
    assert db.upsert_trial(con, "sma_cross@1", "h", {}, "XBTUSD:x", "default") == a
    assert db.upsert_trial(con, "sma_cross@1", "h", {}, "XBTUSD:x", "bybit") != a


def test_run_lifecycle(con):
    db.add_agent(con, cfg())
    with pytest.raises(ValueError, match="trials"):
        db.start_run(con, "a1", "backtest")
    with pytest.raises(ValueError, match="trials"):
        db.start_run(con, "a1", "backtest", 999)
    with pytest.raises(ValueError):
        db.start_run(con, "a1", "live")
    r = db.start_run(con, "a1", "shadow")
    with pytest.raises(ValueError, match="실행 중"):
        db.start_run(con, "a1", "shadow")
    assert db.abandon_running(con, "a1") == 1
    r2 = db.start_run(con, "a1", "shadow")
    db.write_equity(con, r2, "2026-01-01T00:00:00+00:00", 1000)
    db.add_event(con, r2, "2026-01-01T00:00:00+00:00", "signal", side="long")
    db.finish_run(con, r2, "done", {"n": 1})
    runs = db.list_runs(con, "a1")
    assert [x["status"] for x in runs] == ["done", "abandoned"] and r < r2
    assert all(x["evidence"] == 0 for x in runs)
    assert db.events(con, r2)[0]["side"] == "long"
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE runs SET evidence=1 WHERE id=?", (r2,))


def test_commands_taken_once(con, tmp_path):
    db.add_agent(con, cfg())
    db.enqueue_command(con, "a1", "stop")
    with pytest.raises(ValueError):
        db.enqueue_command(con, "a1", "start")
    with pytest.raises(KeyError):
        db.enqueue_command(con, "zz", "stop")
    other = db.connect(tmp_path / "s.sqlite")
    assert db.take_commands(con, "a1") == ["stop"]
    assert db.take_commands(other, "a1") == []


def test_stale_commands_cleared_and_running_unique(con):
    db.add_agent(con, cfg())
    db.enqueue_command(con, "a1", "stop")
    r = db.start_run(con, "a1", "shadow")
    assert db.take_commands(con, "a1") == []
    with pytest.raises(sqlite3.IntegrityError):  # 검사를 우회해도 인덱스가 막는다
        con.execute("INSERT INTO runs (agent_id, kind, status, started_at) VALUES ('a1','paper','running','x')")
    assert r
