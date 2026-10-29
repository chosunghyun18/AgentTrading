"""플랫폼 CLI — `python -m src.cli.at <명령>`.

  strategy list | strategy check <id>
  agent add <yaml> | agent list
  backtest <agent> --start YYYY-MM-DD --end YYYY-MM-DD [--fee-profile default]   # 기본 표본만(OOS 거부)
  run <agent> [--max-bars N] [--equity 1000] [--abandon-stale]                  # shadow·paper (live 없음)
  stop <agent> | status
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta

import pandas as pd

from src.backtest import costs, metrics
from src.backtest.walkforward import check_sample_range
from src.shared import settings as shared_settings
from src.store import db
from src.strategy import agent as agent_mod
from src.strategy import registry, testkit
from src.strategy.engine import batch_run


def _print(rows) -> None:
    for r in rows:
        print("  ".join(f"{k}={v}" for k, v in r.items()))


def cmd_strategy(con, a) -> int:
    if a.action == "list":
        _print([{"id": s.id, "version": s.version, "status": s.status, "summary": s.summary} for s in registry.all()])
        return 0
    failed = testkit.check_all(registry.get(a.id))
    print("통과" if not failed else "실패: " + ", ".join(failed))
    return 1 if failed else 0


def cmd_agent(con, a) -> int:
    if a.action == "add":
        cfg = agent_mod.load(a.path)
        db.add_agent(con, cfg)
        print(f"등록: {cfg.id} ({cfg.strategy}, {cfg.symbol}, {cfg.mode})")
    else:
        _print(db.list_agents(con))
    return 0


def backtest(con, agent_id: str, bars: pd.DataFrame, start, end, fee_profile: str = "default",
             risk_frac: float | None = None) -> tuple[int, dict]:
    """trials 행 → run → 엔진·비용·지표 → 자본곡선·요약 기록. (run_id, 요약)."""
    cfg = agent_mod.from_mapping(db.get_agent(con, agent_id))
    s = registry.get(cfg.strategy)
    p = s.params(cfg.params)
    sample = f"{bars['symbol'].iloc[0]}:{start}~{end}"
    trial = db.upsert_trial(con, s.key, s.params_hash(p), p, sample, fee_profile)
    run_id = db.start_run(con, agent_id, "backtest", trial)
    try:
        res = batch_run(s, p, bars, risk_frac=risk_frac or cfg.risk_frac)
        net = costs.apply_costs(res.roundtrips, fee_profile)
        summary = metrics.summarize_net_run(net, start, end)
        summary.update(skipped_min_qty=res.skipped_min_qty, halted=res.halted, contract="inverse")
        curve = metrics.equity_curve(net, start)
        db.write_equity_many(con, run_id, zip(curve["ts"], curve["equity"]))
    except BaseException as e:
        db.finish_run(con, run_id, "error", {"error": str(e)})
        raise
    db.finish_run(con, run_id, "done", summary)
    return run_id, summary


def cmd_backtest(con, a) -> int:
    from src.ingest.store import load_bars  # 지연 import — 데이터 폴더가 없어도 다른 명령은 동작
    start, end = check_sample_range(a.start, a.end)
    bars = load_bars(start.date(), (end - timedelta(days=1)).date(), shared_settings.get("AT_SYMBOL"))
    run_id, summary = backtest(con, a.agent, bars, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
                               a.fee_profile)
    print(f"run {run_id}\n" + json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return 0


def make_broker(cfg, equity: float):
    from src.runtime.brokers import BybitBroker, NullBroker
    if cfg.mode == "shadow":
        return NullBroker(equity, cfg.risk_frac, cfg.max_leverage)
    if cfg.mode != "paper":  # 세 번째 방어선(YAML·AgentConfig·런타임에 이어)
        raise SystemExit("live 는 지원하지 않는다")
    from src.agent import settings
    from src.agent.bybit import BASE_URLS, Bybit
    from src.agent.service import TradingService
    settings.load_env()
    k = settings.keys("demo")
    client = Bybit(BASE_URLS["demo"], k.api_key if k else None, k.api_secret if k else None)
    svc = TradingService(client, "demo", settings.agent_dir(), settings.risk_file(), symbol=cfg.symbol)
    return BybitBroker(svc, cfg.symbol, cfg.risk_frac, cfg.max_leverage)


def cmd_run(con, a) -> int:
    from src.agent.bybit import BASE_URLS, Bybit
    from src.runtime.brokers import RunAbort
    from src.runtime.feed import run_loop
    from src.runtime.runtime import AgentRuntime
    cfg = agent_mod.from_mapping(db.get_agent(con, a.agent))
    if a.abandon_stale:
        db.abandon_running(con, cfg.id)
    try:
        broker = make_broker(cfg, a.equity)
    except RunAbort as e:
        print(f"시작 거부: {e}", file=sys.stderr)
        return 2
    run_id = db.start_run(con, cfg.id, cfg.mode)
    rt = AgentRuntime(con, cfg, broker, run_id)
    feed_client = Bybit(BASE_URLS["live"])  # 공개 시세 조회 전용 — 키 없음, 주문 불가
    print(f"run {run_id} 시작 ({cfg.mode}, {cfg.symbol}) — 정지: python -m src.cli.at stop {cfg.id}")
    try:
        run_loop(rt, feed_client, cfg.symbol, max_bars=a.max_bars)
    except KeyboardInterrupt:  # 포지션은 남는다(거래소 손절·익절). 정리하려면 `stop` 명령을 쓴다
        db.add_event(con, run_id, pd.Timestamp.now(tz="UTC"), "interrupted", msg="Ctrl+C — 포지션 미정리")
        rt.finish("stopped")
    except Exception as e:
        db.add_event(con, run_id, pd.Timestamp.now(tz="UTC"), "abort", status="error", msg=str(e))
        rt.finish("error")
        raise
    rt.finish("done")
    print(f"run {run_id} 종료: {rt.status}")
    return 0


def cmd_stop(con, a) -> int:
    db.enqueue_command(con, a.agent, "stop")
    print(f"정지 명령 등록: {a.agent} (다음 봉에서 처리)")
    return 0


def cmd_status(con, a) -> int:
    _print(db.list_agents(con))
    print()
    _print([{k: r[k] for k in ("id", "agent_id", "kind", "status", "started_at", "finished_at")}
            for r in db.list_runs(con)[:20]])
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m src.cli.at", description="AgentTrading 플랫폼 CLI")
    ap.add_argument("--store", help="SQLite 경로(기본 AT_STORE_PATH)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("strategy").add_subparsers(dest="action", required=True)
    s.add_parser("list")
    s.add_parser("check").add_argument("id")
    g = sub.add_parser("agent").add_subparsers(dest="action", required=True)
    g.add_parser("add").add_argument("path")
    g.add_parser("list")
    b = sub.add_parser("backtest")
    b.add_argument("agent")
    b.add_argument("--start", required=True)
    b.add_argument("--end", required=True)
    b.add_argument("--fee-profile", default="default", choices=sorted(costs.PROFILES))
    r = sub.add_parser("run")
    r.add_argument("agent")
    r.add_argument("--max-bars", type=int)
    r.add_argument("--equity", type=float, default=1000.0, help="shadow 가상 자본(USDT)")
    r.add_argument("--abandon-stale", action="store_true", help="남아 있는 running run 을 abandoned 로 정리")
    sub.add_parser("stop").add_argument("agent")
    sub.add_parser("status")
    return ap


HANDLERS = {"strategy": cmd_strategy, "agent": cmd_agent, "backtest": cmd_backtest, "run": cmd_run,
            "stop": cmd_stop, "status": cmd_status}


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    con = db.connect(a.store)
    try:
        return HANDLERS[a.cmd](con, a)
    except (ValueError, KeyError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
