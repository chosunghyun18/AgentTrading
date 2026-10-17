"""에이전트 경로 주문 라우터 — REST keep-alive(`Bybit` 의 `requests.Session`), 캐시 기반 리스크 검사.

- 레버리지는 `start()` 에서 한 번만 설정한다(C1 노출 상한 4배). 주문 경로에서는 부르지 않는다(설계 5절).
- 진입 종류: `market`(시장가, 거래소가 IOC 로 처리) · `ioc`(지정가 IOC) · `postonly`(지정가 PostOnly).
  손절은 진입과 함께 거래소 측(`stopLoss`, MarkPrice, Full)으로 건다. 익절은 체결 뒤 `take_profit()` 으로
  reduce-only PostOnly 지정가를 따로 건다(C1 X2). 청산 방향은 `risk.py` 원칙대로 검사하지 않는다.
- 검사용 `OrderRequest` 와 전송 파라미터는 `build_entry()` 한 곳에서 만든다.
- `orderLinkId` = `atg-` + sha1(신호 키)[:32] (36자). 보낸 id 는 `sent_links.txt` 에 먼저 남기고 보낸다 —
  재시작해도 같은 신호를 다시 보내지 않는다. 결과를 모르면(`OrderUnknown`) 재시도하지 않고 `resolve()` 로 조회한다.
- 이 모듈은 asyncio 루프에서 전용 단일 스레드 executor 로 부른다(T-06). 세션을 여러 스레드가 나눠 쓰지 않기 위해서다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.agent import risk
from src.agent.bybit import Bybit, BybitError, definitive_order_rejection, require_order_ack
from src.agent.latency import Trace
from src.agent.live.state_cache import StateCache
from src.agent.risk import OrderRequest, RiskLimits, Rules
from src.agent.service import OrderUnknown, RiskRejected, TradingService

AGENT_LEVERAGE = 4.0             # C1 R3 노출 상한
KINDS = ("market", "ioc", "postonly")
DUPLICATE_LINK = 110072          # orderLinkId 중복(코드 미확인 — 실측 때 확인)


class DuplicateSignal(RuntimeError):
    """같은 신호 키로 이미 주문을 보냈다."""


@dataclass(frozen=True)
class Entry:
    side: str               # Buy | Sell
    qty: float              # BTC
    kind: str               # market | ioc | postonly
    stop_loss: float
    price: float | None = None   # ioc·postonly 지정가


def link_id(key: str) -> str:
    return "atg-" + hashlib.sha1(key.encode()).hexdigest()[:32]


def build_entry(e: Entry, rules: Rules, symbol: str, link: str,
                leverage: float = AGENT_LEVERAGE) -> tuple[OrderRequest, dict]:
    """수량 내림·가격 호가 반올림 → (검사용 요청, 전송 파라미터)."""
    if e.kind not in KINDS:
        raise ValueError(f"진입 종류는 {KINDS}: {e.kind!r}")
    qty = risk.round_qty(e.qty, rules.qty_step)
    sl = risk.round_price(e.stop_loss, rules.tick) if e.stop_loss else e.stop_loss
    limit = e.kind != "market"
    price = risk.round_price(e.price, rules.tick) if limit and e.price else None
    req = OrderRequest(e.side, qty, "Limit" if limit else "Market", leverage, sl, None, price)
    params = {"symbol": symbol, "side": e.side, "orderType": req.order_type, "qty": risk.num_str(qty),
              "orderLinkId": link, "positionIdx": 0, "stopLoss": risk.num_str(sl) if sl else None,
              "slTriggerBy": "MarkPrice", "tpslMode": "Full"}
    if limit:
        params.update(price=risk.num_str(price) if price else None,
                      timeInForce="IOC" if e.kind == "ioc" else "PostOnly")
    return req, params


class OrderRouter:
    def __init__(self, client: Bybit, cache: StateCache, *, mode: str, state_dir: Path, limits_path: Path,
                 symbol: str, live_unlocked: Callable[[], bool], leverage: float = AGENT_LEVERAGE):
        self.client, self.cache, self.mode, self.symbol = client, cache, mode, symbol
        self.state_dir, self.limits_path = Path(state_dir), Path(limits_path)
        self._live_unlocked = live_unlocked
        self.leverage = leverage
        self.rules: Rules | None = None
        self._sent = self._load_sent()
        self._service = TradingService(client, mode, state_dir, limits_path, symbol, live_unlocked)

    # 기동 ---------------------------------------------------------------------------------------
    def _links_path(self) -> Path:
        return self.state_dir / "sent_links.txt"

    def _load_sent(self) -> set[str]:
        try:
            return set(self._links_path().read_text(encoding="utf-8").split())
        except OSError:
            return set()

    def _remember(self, link: str) -> None:
        self._sent.add(link)
        p = self._links_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(link + "\n")
            f.flush()

    def start(self) -> None:
        """상품 규칙 읽기 + 레버리지 1회 설정."""
        self.rules = Rules.from_instrument(self.client.instrument(self.symbol))
        self.client.set_leverage(self.symbol, self.leverage)
        risk.audit(self.state_dir, "live_router_started", mode=self.mode, leverage=self.leverage)

    # 주문 ---------------------------------------------------------------------------------------
    def limits(self) -> RiskLimits | None:
        return risk.load_limits(self.limits_path)

    def submit(self, e: Entry, key: str, last_price: float, trace: Trace | None = None) -> dict:
        """검사 통과 시 진입 주문 1건. 위반이면 `RiskRejected`, 같은 키 재전송이면 `DuplicateSignal`."""
        if self.rules is None:
            raise RuntimeError("start() 를 먼저 부른다")
        link = link_id(key)
        if link in self._sent:
            raise DuplicateSignal(f"이미 보낸 신호: {key} ({link})")
        req, params = build_entry(e, self.rules, self.symbol, link, self.leverage)
        ctx = self.cache.context(req, mode=self.mode, live_unlocked=self._live_unlocked(),
                                 halted=risk.is_halted(self.state_dir), limits=self.limits(), rules=self.rules,
                                 last_price=last_price)
        violations = self.cache.blockers() + risk.check_order(req, ctx)
        if trace:
            trace.mark("t4")
        if violations:
            risk.audit(self.state_dir, "live_order_rejected", mode=self.mode, key=key, params=params,
                       violations=violations)
            raise RiskRejected(violations)
        with self._service._drawdown_entry() as (data, state):
            self._service._drawdown.pending(data, state, self.symbol, link)
            if trace:
                trace.mark("t5")
            try:
                # REST refresh supplements, and never relaxes, cache safety blockers.
                ctx = self._service.context(req)
                violations = self.cache.blockers() + risk.check_order(req, ctx)
                if violations:
                    risk.audit(self.state_dir, "live_order_rejected", mode=self.mode, params=params,
                               violations=violations)
                    raise RiskRejected(violations)
                self._service._drawdown.observe(data, self._service._drawdown.key(self._service._drawdown.identity))
                if self.cache.blockers():
                    raise RiskRejected(self.cache.blockers())
                self._remember(link)
                self._service._drawdown.require_send_ready(data, state, self.symbol, link)
            except Exception as err:
                self._service._drawdown.pending(data, state, self.symbol, link, False)
                if isinstance(err, RiskRejected):
                    raise
                raise RiskRejected([self._service._drawdown.error_text(err)]) from err
            try:
                res = require_order_ack(self.client.create_order(**params), params)
                self._service._drawdown.pending(data, state, self.symbol, link, False)
            except BybitError as err:
                if definitive_order_rejection(err):
                    self._service._drawdown.pending(data, state, self.symbol, link, False)
                else:
                    self._service._drawdown.uncertain(data, state)
                if err.code == DUPLICATE_LINK:
                    risk.audit(self.state_dir, "live_order_duplicate", mode=self.mode, params=params)
                    return {"orderLinkId": link, "duplicate": True}
                risk.audit(self.state_dir, "live_order_error", mode=self.mode, params=params, code=err.code, error="bybit_error")
                raise
            except Exception as err:  # 타임아웃 등 — 접수 여부 불명. 재시도하지 않는다
                self._service._drawdown.uncertain(data, state)
                risk.audit(self.state_dir, "live_order_unknown", mode=self.mode, params=params, error="transport_unknown")
                raise OrderUnknown(link, err) from err
            finally:
                if trace:
                    trace.mark("t5r")
        risk.audit(self.state_dir, "live_order_placed", mode=self.mode, key=key, params=params, result=res)
        return res

    def resolve(self, link: str) -> dict | None:
        """접수 불명 주문을 orderLinkId 로 조회. 없으면 None(접수 여부는 여전히 불명)."""
        rows = self.client.order_by_link(self.symbol, link)
        found = next((o for o in rows if o.get("symbol") == self.symbol and o.get("orderLinkId") == link), None)
        risk.audit(self.state_dir, "live_order_resolved", link=link,
                   status=(found or {}).get("orderStatus", "not_found"))
        return found

    def take_profit(self, position_side: str, qty: float, price: float, key: str) -> dict:
        """보유 포지션 익절 — reduce-only PostOnly 지정가(검사 없음: 위험을 줄이는 방향)."""
        if self.rules is None:
            raise RuntimeError("start() 를 먼저 부른다")
        link = link_id(key + "/tp")
        if link in self._sent:
            raise DuplicateSignal(f"이미 보낸 익절: {key}")
        params = {"symbol": self.symbol, "side": "Sell" if position_side == "Buy" else "Buy", "orderType": "Limit",
                  "qty": risk.num_str(risk.round_qty(qty, self.rules.qty_step)),
                  "price": risk.num_str(risk.round_price(price, self.rules.tick)), "timeInForce": "PostOnly",
                  "reduceOnly": True, "positionIdx": 0, "orderLinkId": link}
        self._remember(link)
        res = self.client.create_order(**params)
        risk.audit(self.state_dir, "live_tp_placed", mode=self.mode, key=key, params=params, result=res)
        return res
