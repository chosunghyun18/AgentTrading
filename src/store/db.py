"""SQLite(WAL) 저장소 — 에이전트·시행(trials)·run·자본곡선·이벤트·명령.

쓰기 주체: run·equity·events 는 런타임/CLI 만, 화면은 `commands` INSERT 만 한다.
명령 소비는 `acked_at` 으로 한 번만(멱등). 모든 run 은 `evidence = 0` 이다(미검증 전략 paper 허용의 조건).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.shared import settings

VERSION = 1
RUN_KINDS = ("backtest", "shadow", "paper")
COMMANDS = ("stop",)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, strategy TEXT NOT NULL, symbol TEXT NOT NULL, mode TEXT NOT NULL,
  config_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trials (
  id INTEGER PRIMARY KEY, strategy TEXT NOT NULL, params_hash TEXT NOT NULL, params_json TEXT NOT NULL,
  sample TEXT NOT NULL, cost_profile TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (strategy, params_hash, sample, cost_profile));
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id), kind TEXT NOT NULL,
  trial_id INTEGER REFERENCES trials(id), evidence INTEGER NOT NULL DEFAULT 0 CHECK (evidence = 0),
  status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, summary_json TEXT);
CREATE TABLE IF NOT EXISTS equity (
  run_id INTEGER NOT NULL REFERENCES runs(id), ts TEXT NOT NULL, equity REAL NOT NULL, PRIMARY KEY (run_id, ts));
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), ts TEXT NOT NULL, kind TEXT NOT NULL,
  data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commands (
  id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, cmd TEXT NOT NULL, created_at TEXT NOT NULL, acked_at TEXT);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
CREATE INDEX IF NOT EXISTS commands_open ON commands(agent_id, acked_at);
CREATE UNIQUE INDEX IF NOT EXISTS one_running_run ON runs(agent_id) WHERE status = 'running' AND kind != 'backtest';
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts(ts) -> str:
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


def default_path() -> Path:
    return Path(settings.get("AT_STORE_PATH"))


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """연결 1개(스레드·프로세스마다 따로). 스키마가 없으면 만든다."""
    path = Path(path or default_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA foreign_keys=ON")
    migrate(con)
    return con


def migrate(con: sqlite3.Connection) -> None:
    v = con.execute("PRAGMA user_version").fetchone()[0]
    if v > VERSION:
        raise RuntimeError(f"저장소 버전 {v} > 코드 {VERSION} — 코드를 업데이트한다")
    if v < VERSION:
        con.executescript(SCHEMA)
        con.execute(f"PRAGMA user_version={VERSION}")


def _json(d) -> str:
    return json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)


# 에이전트 -----------------------------------------------------------------------------------------------

def add_agent(con, cfg) -> None:
    """`AgentConfig` 등록. 같은 id 거부, paper 는 심볼당 1개."""
    if con.execute("SELECT 1 FROM agents WHERE id=?", (cfg.id,)).fetchone():
        raise ValueError(f"이미 있는 에이전트: {cfg.id}")
    if cfg.mode == "paper" and con.execute(
            "SELECT 1 FROM agents WHERE mode='paper' AND symbol=?", (cfg.symbol,)).fetchone():
        raise ValueError(f"paper 에이전트는 심볼당 1개: {cfg.symbol} 에 이미 있다")
    con.execute("INSERT INTO agents VALUES (?,?,?,?,?,?)",
                (cfg.id, cfg.strategy, cfg.symbol, cfg.mode, _json(cfg.to_dict()), now()))


def get_agent(con, agent_id: str) -> dict:
    row = con.execute("SELECT config_json FROM agents WHERE id=?", (agent_id,)).fetchone()
    if row is None:
        raise KeyError(f"모르는 에이전트: {agent_id}")
    return json.loads(row[0])


def list_agents(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT a.id, a.strategy, a.symbol, a.mode, a.created_at, "
        "(SELECT status FROM runs WHERE agent_id=a.id ORDER BY id DESC LIMIT 1) AS last_status "
        "FROM agents a ORDER BY a.id")]


# 시행·run -------------------------------------------------------------------------------------------------

def upsert_trial(con, strategy: str, params_hash: str, params: dict, sample: str, cost_profile: str) -> int:
    con.execute("INSERT OR IGNORE INTO trials (strategy, params_hash, params_json, sample, cost_profile, created_at) "
                "VALUES (?,?,?,?,?,?)", (strategy, params_hash, _json(params), sample, cost_profile, now()))
    return con.execute("SELECT id FROM trials WHERE strategy=? AND params_hash=? AND sample=? AND cost_profile=?",
                       (strategy, params_hash, sample, cost_profile)).fetchone()[0]


def start_run(con, agent_id: str, kind: str, trial_id: int | None = None) -> int:
    """backtest 는 trials 행이 있어야 한다. shadow·paper 는 같은 에이전트의 running run 이 있으면 거부."""
    if kind not in RUN_KINDS:
        raise ValueError(f"run 종류는 {RUN_KINDS}: {kind!r}")
    get_agent(con, agent_id)
    if kind == "backtest":
        if trial_id is None or not con.execute("SELECT 1 FROM trials WHERE id=?", (trial_id,)).fetchone():
            raise ValueError("백테스트는 trials 행을 먼저 기록해야 한다")
    elif con.execute("SELECT 1 FROM runs WHERE agent_id=? AND kind!='backtest' AND status='running'",
                     (agent_id,)).fetchone():
        raise ValueError(f"{agent_id} 는 이미 실행 중인 run 이 있다(`abandon` 으로 정리 후 시작)")
    try:
        cur = con.execute("INSERT INTO runs (agent_id, kind, trial_id, status, started_at) VALUES (?,?,?,?,?)",
                          (agent_id, kind, trial_id, "running", now()))
    except sqlite3.IntegrityError:  # 동시에 시작한 다른 프로세스(부분 유일 인덱스)
        raise ValueError(f"{agent_id} 는 이미 실행 중인 run 이 있다") from None
    if kind != "backtest":  # 시작 전에 쌓인 명령은 이 run 에 적용하지 않는다
        con.execute("UPDATE commands SET acked_at=? WHERE agent_id=? AND acked_at IS NULL", (now(), agent_id))
    return cur.lastrowid


def finish_run(con, run_id: int, status: str, summary: dict | None = None) -> None:
    con.execute("UPDATE runs SET status=?, finished_at=?, summary_json=? WHERE id=?",
                (status, now(), _json(summary or {}), run_id))


def abandon_running(con, agent_id: str) -> int:
    """비정상 종료로 남은 running run 을 abandoned 로. 바뀐 행 수."""
    return con.execute("UPDATE runs SET status='abandoned', finished_at=? WHERE agent_id=? AND status='running'",
                       (now(), agent_id)).rowcount


def list_runs(con, agent_id: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM runs", ()
    if agent_id:
        q, args = q + " WHERE agent_id=?", (agent_id,)
    return [dict(r) for r in con.execute(q + " ORDER BY id DESC", args)]


# 기록 ----------------------------------------------------------------------------------------------------

def write_equity(con, run_id: int, ts, equity: float) -> None:
    con.execute("INSERT OR REPLACE INTO equity VALUES (?,?,?)", (run_id, _ts(ts), float(equity)))


def write_equity_many(con, run_id: int, rows) -> None:
    con.execute("BEGIN")
    try:
        con.executemany("INSERT OR REPLACE INTO equity VALUES (?,?,?)",
                        [(run_id, _ts(t), float(e)) for t, e in rows])
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def add_event(con, run_id: int, ts, kind: str, **data) -> None:
    con.execute("INSERT INTO events (run_id, ts, kind, data_json) VALUES (?,?,?,?)",
                (run_id, _ts(ts), kind, _json(data)))


def equity(con, run_id: int) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT ts, equity FROM equity WHERE run_id=? ORDER BY ts", (run_id,))]


def events(con, run_id: int, limit: int = 500) -> list[dict]:
    rows = con.execute("SELECT id, ts, kind, data_json FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?",
                       (run_id, limit))
    return [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], **json.loads(r["data_json"])} for r in rows]


# 명령 ----------------------------------------------------------------------------------------------------

def enqueue_command(con, agent_id: str, cmd: str) -> int:
    if cmd not in COMMANDS:
        raise ValueError(f"명령은 {COMMANDS}: {cmd!r}")
    get_agent(con, agent_id)
    return con.execute("INSERT INTO commands (agent_id, cmd, created_at) VALUES (?,?,?)",
                       (agent_id, cmd, now())).lastrowid


def take_commands(con, agent_id: str) -> list[str]:
    """미처리 명령을 꺼내며 ack(한 번만 돌려준다)."""
    rows = con.execute("SELECT id, cmd FROM commands WHERE agent_id=? AND acked_at IS NULL ORDER BY id",
                       (agent_id,)).fetchall()
    return [r["cmd"] for r in rows
            if con.execute("UPDATE commands SET acked_at=? WHERE id=? AND acked_at IS NULL",
                           (now(), r["id"])).rowcount == 1]
