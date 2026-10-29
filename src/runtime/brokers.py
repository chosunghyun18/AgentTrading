"""브로커 2종.

- `NullBroker`(shadow): 주문 없음. 백테스트와 같은 `Sim` 으로 가상 체결 → 같은 봉이면 진입 시각·방향이 백테스트와 같다.
- `BybitBroker`(paper): 기존 `TradingService(mode="demo")` 로만 주문 → 리스크 한도·MDD·감사 로그를 그대로 거친다.
  신호 봉 close 직후 시장가(백테스트의 "다음 봉 open" 과 몇 초 차이), 손절·익절은 거래소 MarkPrice 트리거라
  백테스트(봉 고가·저가)와 **같지 않다**(알려진 차이). 진입·청산을 다시 보내지 않는다(응답 불명 = run 중단).
"""

from __future__ import annotations

from src.agent.bybit import BybitError
from src.agent.risk import OrderRequest
from src.agent.service import OrderUnknown, RiskRejected, TradingService
from src.contracts.linear import Linear
from src.strategy.base import Signal
from src.strategy.sim import Sim


class RunAbort(RuntimeError):
    """런타임을 멈춰야 하는 상황(주문 결과 불명·정지 상태 등). run 상태는 `status` 로 남긴다."""

    def __init__(self, status: str, msg: str):
        self.status = status
        super().__init__(msg)


MAX_READ_FAILS = 5


class SkipBar(RuntimeError):
    """조회 일시 실패 — 이번 봉의 판단을 건너뛴다(주문 없음)."""


class NullBroker:
    mode = "shadow"

    def __init__(self, equity: float = 1000.0, risk_frac: float = 0.01, max_leverage: float = 2.0, qty_step=0.001):
        self.sim = Sim(Linear(qty_step), equity, risk_frac, max_exposure=max_leverage)

    def step(self, bar) -> list[dict]:
        return self.sim.step(bar)

    def busy(self) -> bool:
        return self.sim.busy()

    def blocked(self) -> bool:
        return self.sim.blocked()

    def submit(self, sig: Signal, bar) -> list[dict]:
        self.sim.submit(sig, bar["ts"])
        return [{"kind": "pending", "side": sig.side}]

    def equity(self) -> float:
        return self.sim.equity

    def on_stop(self) -> list[dict]:
        return []


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class BybitBroker:
    mode = "paper"

    def __init__(self, svc: TradingService, symbol: str, risk_frac: float, max_leverage: float):
        if svc.mode != "demo":
            raise RunAbort("refused", f"paper 는 demo 서비스만: mode={svc.mode!r}")
        if svc.symbol != symbol:
            raise RunAbort("refused", f"서비스 심볼 {svc.symbol} ≠ 에이전트 심볼 {symbol}")
        if not svc.client.has_keys:
            raise RunAbort("refused", "Bybit Demo API 키가 없다(.env BYBIT_DEMO_API_KEY/SECRET)")
        limits = svc.limits()
        if limits is None:
            raise RunAbort("refused", "리스크 한도 미설정 — 콘솔 '리스크' 화면에서 저장한다")
        if not svc.drawdown_status().get("enrolled"):
            raise RunAbort("refused", "MDD 계정 등록 전 — `python -m src.agent.drawdown_cli` 로 initialize")
        self.svc, self.risk_frac = svc, risk_frac
        self.leverage = min(max_leverage, limits.max_leverage)
        self.rules = svc.rules()
        self.contract = Linear(self.rules.qty_step)
        self.i = -1
        self.entry_i: int | None = None
        self.max_hold: int | None = None
        self.adopted = False
        self.read_fails = 0

    def _wallet_equity(self) -> float:
        return _f(self.svc.client.wallet().get("totalEquity"))

    def _read(self, fn, what: str):
        """조회 호출. 일시 오류는 이번 봉을 건너뛰고(None), 연속 `MAX_READ_FAILS` 번이면 run 을 멈춘다."""
        try:
            v = fn()
        except Exception as e:  # noqa: BLE001 — 조회는 부작용이 없어 다음 봉에 다시 읽는다
            self.read_fails += 1
            if self.read_fails >= MAX_READ_FAILS:
                raise RunAbort("error", f"{what} 조회 연속 실패 {self.read_fails}회: {type(e).__name__}") from e
            raise SkipBar(f"{what} 조회 실패: {type(e).__name__}") from e
        self.read_fails = 0
        return v

    def step(self, bar) -> list[dict]:
        self.i += 1
        events = []
        pos = self._read(self.svc.position, "포지션")
        if pos is not None and self.entry_i is None and not self.adopted:
            # 재시작 등으로 이 run 이 열지 않은 포지션 — 거래소 손절·익절만 관리한다(max_hold 미적용)
            self.adopted = True
            events.append({"kind": "adopted", "msg": "기존 포지션 — 거래소 손절·익절로만 관리, max_hold 미적용"})
        if pos is None:
            self.adopted = False
            if self.entry_i is not None:
                events.append({"kind": "exit", "reason": "exchange"})  # 거래소 손절·익절 또는 외부 청산
                self.entry_i = None
        elif self.entry_i is not None and self.max_hold and self.i - self.entry_i >= self.max_hold:
            res = self._close("agent_max_hold")
            events.append({"kind": "close", "reason": "time", "result": res})
            if "error" not in res:
                self.entry_i = None  # 실패하면 다음 봉에 다시 시도
        return events

    def _close(self, reason: str) -> dict:
        try:
            res = self.svc.close_position(reason)
        except BybitError as e:
            return {"error": f"bybit {e.code}"}
        except Exception as e:  # noqa: BLE001 — 재시도하지 않는다
            raise RunAbort("uncertain", f"청산 결과 불명: {type(e).__name__}") from e
        return {"orderId": (res or {}).get("orderId")}

    def busy(self) -> bool:
        return self._read(self.svc.position, "포지션") is not None  # 실제 포지션 기준 — 중복 진입 방지

    def blocked(self) -> bool:
        if self._read(self.svc.halted, "정지 상태"):
            raise RunAbort("halted", "HALT(킬스위치·정지) 상태 — run 을 멈춘다")
        return False

    def submit(self, sig: Signal, bar) -> list[dict]:
        ref = float(bar["close"])
        d = 1 if sig.side == "long" else -1
        try:
            qty, _ = self.contract.size(self._read(self._wallet_equity, "자본"), ref, sig.side, sig.stop_frac, self.risk_frac,
                                        max_exposure=self.leverage)
        except ValueError as e:
            return [{"kind": "error", "stage": "sizing", "msg": str(e)}]
        if qty < self.rules.min_qty or qty * ref < self.rules.min_notional:
            return [{"kind": "skip", "reason": "min_qty", "qty": qty}]
        req = OrderRequest("Buy" if d > 0 else "Sell", qty, "Market", self.leverage,
                           ref * (1 - d * sig.stop_frac), ref * (1 + d * sig.tp_r * sig.stop_frac) if sig.tp_r else None)
        try:
            res = self.svc.place_order(req)
        except RiskRejected as e:
            return [{"kind": "rejected", "violations": e.violations}]
        except OrderUnknown as e:
            raise RunAbort("uncertain", str(e)) from e
        except BybitError as e:
            return [{"kind": "error", "stage": "order", "code": e.code, "msg": str(e)}]
        self.entry_i, self.max_hold = self.i, sig.max_hold
        return [{"kind": "order", "side": sig.side, "qty": qty, "ref": ref, "stop": req.stop_loss,
                 "tp": req.take_profit, "orderId": res.get("orderId")}]

    def equity(self) -> float | None:
        try:
            return self._wallet_equity()
        except Exception:  # noqa: BLE001 — 자본 기록만 건너뛴다
            return None

    def on_stop(self) -> list[dict]:
        """정지: 포지션을 시장가로 정리. 정리 실패면 run 을 uncertain 으로 끝낸다(사람 확인)."""
        if self._read(self.svc.position, "포지션") is None:
            return []
        res = self._close("agent_stop")
        if "error" in res:
            raise RunAbort("uncertain", f"정지 중 청산 실패({res['error']}) — 포지션이 열려 있다. 콘솔에서 확인")
        return [{"kind": "close", "reason": "stop", "result": res}]
