"""W1 최소 — CLI `python -m src.cli.at`."""

import pytest

from src.cli import at
from src.store import db
from src.strategy.testkit import synthetic_bars


@pytest.fixture
def store(tmp_path):
    return str(tmp_path / "s.sqlite")


def _yaml(tmp_path, body):
    p = tmp_path / "a.yaml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_strategy_commands(store, capsys):
    assert at.main(["--store", store, "strategy", "list"]) == 0
    assert "sma_cross" in capsys.readouterr().out
    assert at.main(["--store", store, "strategy", "check", "sma_cross"]) == 0
    assert at.main(["--store", store, "strategy", "check", "nope"]) == 2


def test_agent_add_list_and_live_refused(store, tmp_path, capsys):
    assert at.main(["--store", store, "agent", "add", _yaml(tmp_path, "id: s1\nstrategy: sma_cross\nmode: shadow\n")]) == 0
    assert at.main(["--store", store, "agent", "add", _yaml(tmp_path, "id: l1\nstrategy: sma_cross\nmode: live\n")]) == 2
    assert "live" in capsys.readouterr().err
    at.main(["--store", store, "agent", "list"])
    out = capsys.readouterr().out
    assert "id=s1" in out and "l1" not in out


def test_backtest_records_trial_run_equity(store, tmp_path):
    at.main(["--store", store, "agent", "add", _yaml(tmp_path, "id: s1\nstrategy: sma_cross\nmode: shadow\n")])
    con = db.connect(store)
    bars = synthetic_bars(3 * 1440, start="2019-06-01")
    run_id, summary = at.backtest(con, "s1", bars, "2019-06-01", "2019-06-04")
    assert summary["n_trades"] > 0 and summary["contract"] == "inverse"
    run = db.list_runs(con, "s1")[0]
    assert run["id"] == run_id and run["kind"] == "backtest" and run["status"] == "done" and run["trial_id"]
    assert con.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 1
    assert len(db.equity(con, run_id)) == summary["n_trades"] + 1
    at.backtest(con, "s1", bars, "2019-06-01", "2019-06-04")  # 같은 시행은 trials 1행 유지
    assert con.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 1


def test_backtest_refuses_oos(store, tmp_path, capsys):
    at.main(["--store", store, "agent", "add", _yaml(tmp_path, "id: s1\nstrategy: sma_cross\nmode: shadow\n")])
    assert at.main(["--store", store, "backtest", "s1", "--start", "2022-01-01", "--end", "2022-02-01"]) == 2
    assert "기본 표본" in capsys.readouterr().err


def test_stop_and_status(store, tmp_path, capsys):
    at.main(["--store", store, "agent", "add", _yaml(tmp_path, "id: s1\nstrategy: sma_cross\nmode: shadow\n")])
    assert at.main(["--store", store, "stop", "s1"]) == 0
    assert db.take_commands(db.connect(store), "s1") == ["stop"]
    assert at.main(["--store", store, "status"]) == 0


def test_paper_run_refused_without_keys(store, tmp_path, monkeypatch, capsys):
    from src.agent import settings
    monkeypatch.setattr(settings, "load_env", lambda: None)
    monkeypatch.delenv("BYBIT_DEMO_API_KEY", raising=False)
    monkeypatch.setenv("AT_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AT_RISK_FILE", str(tmp_path / "none.yaml"))
    at.main(["--store", store, "agent", "add", _yaml(tmp_path, "id: p1\nstrategy: sma_cross\nmode: paper\n")])

    class NoNet:
        def __init__(self, *a, **k):
            self.has_keys = False
    monkeypatch.setattr("src.agent.bybit.Bybit", NoNet)
    assert at.main(["--store", store, "run", "p1", "--max-bars", "1"]) == 2
    assert "키" in capsys.readouterr().err
    assert db.list_runs(db.connect(store), "p1") == []
