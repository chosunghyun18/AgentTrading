"""개인 WS 상태 캐시 — 주문 직전 REST 조회 없이 `risk.Context` 를 만든다(설계 5절 ④).

- 토픽: `position`·`order`·`execution`·`wallet`. 거래소 메시지를 그대로 적용한다.
- 오늘 실현 손익 = `cumRealisedPnl` − 자정 기준값. 기준값은 REST 스냅샷(기동·자정·재대조)에서
  `positions.cumRealisedPnl − 오늘 closed_pnl 합` 으로 맞추고, 자정이 지나면 그 순간의 `cumRealisedPnl` 로 바꾼다.
  `cumRealisedPnl` 과 `closedPnl` 의 수수료·펀딩 포함 정의가 같은지는 미확인 — `reconcile` 이 REST 값과 대조한다.
- 신규 주문을 막는 경우(`blockers()`): REST 스냅샷 전, 개인 WS 끊김(재연결 후 스냅샷 전까지), REST 대조
  불일치 2회 연속(D5). 막힌 상태는 REST 스냅샷을 다시 적용(`load_snapshot`)해야 풀린다 — 거래소가 기준이다.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from src.agent.risk import Context, OrderRequest, RiskLimits, Rules

DAY_MS = 86_400_000
OPEN_STATUSES = ("New", "PartiallyFilled", "Untriggered")
MISMATCH_LIMIT = 2      # 연속 불일치 횟수 — 스냅샷과 WS 푸시가 엇갈린 일시 차이는 한 번 봐준다
PNL_TOL = 0.01          # USDT


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def day_start(ms: float) -> int:
    return int(ms) - int(ms) % DAY_MS


@dataclass(frozen=True)
class Snapshot:
    """REST 로 읽은 거래소 상태."""
    position: dict | None          # positions() 중 대상 심볼 1행(one-way)
    open_orders: list[dict]
    wallet: dict
    closed_pnl_today: float        # 오늘(UTC) closed_pnl 합
    now_ms: int


def signed_qty(pos: dict | None) -> float:
    if not pos:
        return 0.0
    side = pos.get("side")
    return _f(pos.get("size")) * (1 if side == "Buy" else -1 if side == "Sell" else 0)


class StateCache:
    def __init__(self, symbol: str, audit: Callable[..., object] | None = None):
        self.symbol = symbol
        self._audit = audit or (lambda *a, **k: None)
        self.position: dict = {}
        self.orders: dict[str, dict] = {}     # orderId → 최근 상태(열린 것만 보관)
        self.wallet: dict = {}
        self.baseline: float | None = None     # 자정 기준 cumRealisedPnl
        self.day_ms: int | None = None
        self.connected = False
        self.ready = False
        self.mismatches = 0
        self.blocked: str | None = None
        self.listeners: list[Callable[[str, dict], None]] = []   # (토픽, 항목) — 지연 기록용

    # 스냅샷·연결 ---------------------------------------------------------------------------------
    def load_snapshot(self, s: Snapshot) -> None:
        was = self.blocked
        self.position = dict(s.position or {})
        self.orders = {o["orderId"]: dict(o) for o in s.open_orders if o.get("orderStatus") in OPEN_STATUSES}
        self.wallet = dict(s.wallet)
        self.baseline = _f(self.position.get("cumRealisedPnl")) - s.closed_pnl_today
        self.day_ms = day_start(s.now_ms)
        self.ready, self.mismatches, self.blocked = True, 0, None
        if was:
            self._audit("live_cache_unblocked", previous=was)

    def set_connected(self, ok: bool) -> None:
        if not ok and self.connected:
            self._audit("live_private_ws_down")
        self.connected = ok
        if not ok:
            self.ready = False   # 끊긴 동안의 변화를 모른다 → 재연결 후 스냅샷 필요

    def roll_day(self, now_ms: float) -> bool:
        """UTC 자정이 지났으면 기준값을 지금의 cumRealisedPnl 로 바꾼다."""
        d = day_start(now_ms)
        if self.day_ms is None or d <= self.day_ms:
            return False
        self.day_ms, self.baseline = d, _f(self.position.get("cumRealisedPnl"))
        return True

    # 스트림 적용 ---------------------------------------------------------------------------------
    def apply(self, msg: dict) -> None:
        topic = str(msg.get("topic", "")).split(".")[0]
        for item in msg.get("data") or []:
            if topic != "wallet" and item.get("symbol") != self.symbol:
                continue
            if topic == "position":
                self.position.update(item)
            elif topic == "order":
                if item.get("orderStatus") in OPEN_STATUSES:
                    self.orders[item["orderId"]] = dict(item)
                else:
                    self.orders.pop(item.get("orderId"), None)
            elif topic == "wallet":
                self.wallet.update(item)
            for fn in self.listeners:
                fn(topic, item)

    # 조회 ---------------------------------------------------------------------------------------
    @property
    def position_qty(self) -> float:
        return signed_qty(self.position)

    @property
    def today_pnl(self) -> float:
        if self.baseline is None:
            return float("nan")
        return _f(self.position.get("cumRealisedPnl")) - self.baseline + _f(self.position.get("unrealisedPnl"))

    @property
    def open_order_qty(self) -> float:
        return sum(_f(o.get("qty")) - _f(o.get("cumExecQty")) for o in self.orders.values() if not o.get("reduceOnly"))

    @property
    def available(self) -> float:
        return _f(self.wallet.get("totalAvailableBalance"), float("nan"))

    def blockers(self) -> list[str]:
        v = []
        if not self.connected:
            v.append("개인 WS 연결 없음")
        if not self.ready:
            v.append("상태 캐시 미초기화 — REST 스냅샷 필요")
        if self.blocked:
            v.append(f"REST 대조 불일치로 신규 주문 중단: {self.blocked}")
        if not math.isfinite(self.today_pnl) or not math.isfinite(self.available):
            v.append("손익·잔고를 알 수 없다")
        return v

    def context(self, req: OrderRequest, *, mode: str, live_unlocked: bool, halted: bool,
                limits: RiskLimits | None, rules: Rules, last_price: float) -> Context:
        ref = req.price if req.order_type == "Limit" and req.price else last_price
        return Context(mode=mode, live_unlocked=live_unlocked, halted=halted, limits=limits, ref_price=ref,
                       position_qty=self.position_qty, today_pnl=self.today_pnl, rules=rules,
                       position_avg=_f(self.position.get("avgPrice") or self.position.get("entryPrice")),
                       open_order_qty=self.open_order_qty, available=self.available)

    # 대조(D5) -----------------------------------------------------------------------------------
    def reconcile(self, s: Snapshot, qty_eps: float = 1e-9) -> list[str]:
        """캐시 vs REST. 비교: 포지션 수량, 열린 주문 ID 집합, cumRealisedPnl. 2회 연속 불일치면 차단."""
        diffs = []
        if abs(self.position_qty - signed_qty(s.position)) > qty_eps:
            diffs.append(f"포지션 {self.position_qty:g} ≠ REST {signed_qty(s.position):g}")
        rest_ids = {o["orderId"] for o in s.open_orders if o.get("orderStatus") in OPEN_STATUSES}
        if set(self.orders) != rest_ids:
            diffs.append(f"열린 주문 {sorted(self.orders)} ≠ REST {sorted(rest_ids)}")
        mine, theirs = _f(self.position.get("cumRealisedPnl")), _f((s.position or {}).get("cumRealisedPnl"))
        if abs(mine - theirs) > PNL_TOL:
            diffs.append(f"cumRealisedPnl {mine:g} ≠ REST {theirs:g}")
        if not diffs:
            self.mismatches = 0
            return diffs
        self.mismatches += 1
        self._audit("live_reconcile_mismatch", count=self.mismatches, diffs=diffs)
        if self.mismatches >= MISMATCH_LIMIT and not self.blocked:
            self.blocked = "; ".join(diffs)
            self._audit("live_cache_blocked", reason=self.blocked)
        return diffs
