"""`AgentRuntime` — 봉 하나씩 받아 브로커와 저장소를 움직인다. 백테스트 엔진과 같은 전략 호출 규칙."""

from __future__ import annotations

from collections import deque

import pandas as pd

from src.store import db
from src.strategy import registry
from src.strategy.agent import AgentConfig
from src.strategy.base import WINDOW_COLUMNS
from src.runtime.brokers import RunAbort, SkipBar

ONE_MIN = pd.Timedelta(minutes=1)


class AgentRuntime:
    def __init__(self, con, cfg: AgentConfig, broker, run_id: int):
        if cfg.mode not in ("shadow", "paper") or broker.mode != cfg.mode:
            raise ValueError(f"모드 불일치·미지원: agent={cfg.mode} broker={broker.mode}")
        self.con, self.cfg, self.broker, self.run_id = con, cfg, broker, run_id
        self.strategy = registry.get(cfg.strategy)
        self.p = self.strategy.params(cfg.params)
        self.w = self.strategy.warmup(self.p)
        self.window: deque = deque(maxlen=self.w)
        self.last_ts: pd.Timestamp | None = None
        self.status: str | None = None  # 끝나면 stopped·halted·uncertain·error

    def _log(self, ts, events):
        for e in events:
            e = dict(e)
            db.add_event(self.con, self.run_id, ts, e.pop("kind"), **e)

    def _push(self, bar) -> bool:
        """창에 넣는다. 중복·과거 봉이면 False, 1분 넘는 공백이면 창을 비운다(특징값 연속성)."""
        ts = pd.Timestamp(bar["ts"])
        if self.last_ts is not None and ts <= self.last_ts:
            return False
        if self.last_ts is not None and ts - self.last_ts != ONE_MIN:
            db.add_event(self.con, self.run_id, ts, "gap", after=self.last_ts.isoformat())
            self.window.clear()
        self.window.append({k: bar[k] for k in WINDOW_COLUMNS})
        self.last_ts = ts
        return True

    def prime(self, bars) -> None:
        """과거 봉으로 창만 채운다(신호 평가·주문 없음)."""
        for b in bars:
            self._push(b)

    def on_bar(self, bar) -> None:
        if self.status is not None:
            return
        ts = pd.Timestamp(bar["ts"])
        if self.last_ts is not None and ts <= self.last_ts:
            return
        try:
            self._log(ts, self.broker.step(bar))
            self._push(bar)
            if (len(self.window) == self.w and not self.broker.busy() and not self.broker.blocked()):
                sig = self.strategy.on_bar(pd.DataFrame(list(self.window)), self.p)
                if sig is not None:
                    db.add_event(self.con, self.run_id, ts, "signal", side=sig.side, stop_frac=sig.stop_frac,
                                 tp_r=sig.tp_r, max_hold=sig.max_hold)
                    self._log(ts, self.broker.submit(sig, bar))
            eq = self.broker.equity()
            if eq is not None:
                db.write_equity(self.con, self.run_id, ts, eq)
        except SkipBar as e:
            db.add_event(self.con, self.run_id, ts, "skip_bar", msg=str(e))
            self._push(bar)
        except RunAbort as e:
            db.add_event(self.con, self.run_id, ts, "abort", status=e.status, msg=str(e))
            self.finish(e.status)
            return
        try:
            if "stop" in db.take_commands(self.con, self.cfg.id):
                self._log(ts, self.broker.on_stop())
                self.finish("stopped")
        except (RunAbort, SkipBar) as e:
            db.add_event(self.con, self.run_id, ts, "abort", status=getattr(e, "status", "uncertain"), msg=str(e))
            self.finish(getattr(e, "status", "uncertain"))

    def finish(self, status: str) -> None:
        if self.status is None:
            self.status = status
            db.finish_run(self.con, self.run_id, status, {"last_ts": self.last_ts and self.last_ts.isoformat()})
