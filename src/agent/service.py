"""트레이딩 콘솔 서비스: 거래소 클라이언트 + 리스크 검사 + 정지/킬스위치 + 감사 로그.

화면은 이 계층만 부른다. 신규 주문은 실행 직전에 시세·포지션·손익을 다시 읽어 `risk.check_order` 를 통과해야
나간다(미리보기 뒤 상황이 바뀌어도 안전). 모든 주문·취소·청산·설정 변경은 감사 로그에 남긴다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from src.agent import risk, settings
from src.agent.bybit import BASE_URLS, Bybit, BybitError
from src.agent.risk import Context, OrderRequest, RiskLimits, Rules


class OrderUnknown(RuntimeError):
    """응답을 못 받아 주문 접수 여부를 모른다 — 미체결·포지션을 직접 확인해야 한다."""

    def __init__(self, link_id: str, cause: Exception):
        self.link_id = link_id
        super().__init__(f"주문 결과 불명({type(cause).__name__}) — 미체결·포지션을 확인한다(orderLinkId {link_id})")


class RiskRejected(RuntimeError):
    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__("; ".join(violations))


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def utc_midnight_ms(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


class TradingService:
    def __init__(self, client: Bybit, mode: str, state_dir: Path, limits_path: Path, symbol: str = "BTCUSDT",
                 live_unlocked: Callable[[], bool] = settings.live_unlocked):
        self.client, self.mode, self.symbol = client, mode, symbol
        self.state_dir, self.limits_path = Path(state_dir), Path(limits_path)
        self._live_unlocked = live_unlocked
        self._rules: Rules | None = None

    # 조회 ---------------------------------------------------------------------------------------------
    def rules(self) -> Rules:
        if self._rules is None:
            self._rules = Rules.from_instrument(self.client.instrument(self.symbol))
        return self._rules

    def limits(self) -> RiskLimits | None:
        return risk.load_limits(self.limits_path)

    def halted(self) -> bool:
        return risk.is_halted(self.state_dir)

    def position(self) -> dict | None:
        """열린 포지션 1개(one-way) 또는 None."""
        for p in self.client.positions(self.symbol):
            if _f(p.get("size")) > 0:
                return p
        return None

    @staticmethod
    def position_qty(pos: dict | None) -> float:
        if not pos:
            return 0.0
        return _f(pos["size"]) if pos.get("side") == "Buy" else -_f(pos["size"])

    def today_pnl(self, pos: dict | None = None) -> float:
        """오늘(UTC) 실현 손익 합 + 현재 미실현 손익."""
        realized = sum(_f(r.get("closedPnl")) for r in self.client.closed_pnl(self.symbol, utc_midnight_ms()))
        return realized + (_f(pos.get("unrealisedPnl")) if pos else 0.0)

    def open_order_qty(self) -> float:
        """미체결 신규(비 reduce-only) 주문 수량 합 — 체결되면 노출이 되는 몫."""
        return sum(_f(o.get("qty")) - _f(o.get("cumExecQty")) for o in self.client.open_orders(self.symbol)
                   if not o.get("reduceOnly"))

    def available(self) -> float:
        return _f(self.client.wallet().get("totalAvailableBalance"), float("inf"))

    def context(self, req: OrderRequest) -> Context:
        pos = self.position()
        ref = req.price if req.order_type == "Limit" and req.price else _f(self.client.ticker(self.symbol)["lastPrice"])
        return Context(mode=self.mode, live_unlocked=self._live_unlocked(), halted=self.halted(),
                       limits=self.limits(), ref_price=ref, position_qty=self.position_qty(pos),
                       today_pnl=self.today_pnl(pos), rules=self.rules(),
                       position_avg=_f((pos or {}).get("avgPrice")), open_order_qty=self.open_order_qty(),
                       available=self.available())

    def review(self, req: OrderRequest) -> tuple[list[str], dict, Context]:
        ctx = self.context(req)
        return risk.check_order(req, ctx), risk.preview(req, ctx), ctx

    # 주문 ---------------------------------------------------------------------------------------------
    def normalize(self, req: OrderRequest) -> OrderRequest:
        """수량은 수량 단위로 내림, 가격들은 호가 단위로 반올림."""
        r = self.rules()
        rp = lambda x: risk.round_price(x, r.tick) if x else x  # noqa: E731
        return OrderRequest(req.side, risk.round_qty(req.qty, r.qty_step), req.order_type, req.leverage,
                            rp(req.stop_loss), rp(req.take_profit), rp(req.price) if req.order_type == "Limit" else None)

    def place_order(self, req: OrderRequest, confirm_live: bool = False) -> dict:
        """검사 통과 시 레버리지 설정 → 주문(거래소 측 손절·익절 포함). 위반이면 `RiskRejected`(주문 안 나감).

        LIVE 는 `confirm_live=True` 가 있어야 한다(화면 확인을 서비스 계층에서도 강제).
        """
        req = self.normalize(req)
        violations, prev, ctx = self.review(req)
        if self.mode == "live" and not confirm_live:
            violations = [*violations, "LIVE 주문 확인이 없다"]
        if violations:
            risk.audit(self.state_dir, "order_rejected", mode=self.mode, request=req.__dict__, violations=violations)
            raise RiskRejected(violations)
        params = {"symbol": self.symbol, "side": req.side, "orderType": req.order_type, "qty": risk.num_str(req.qty),
                  "orderLinkId": f"atc-{uuid.uuid4().hex[:20]}", "positionIdx": 0,
                  "stopLoss": risk.num_str(req.stop_loss), "slTriggerBy": "MarkPrice", "tpslMode": "Full"}
        if req.take_profit:
            params.update(takeProfit=risk.num_str(req.take_profit), tpTriggerBy="MarkPrice")
        if req.order_type == "Limit":
            params.update(price=risk.num_str(req.price), timeInForce="GTC")
        try:
            self.client.set_leverage(self.symbol, req.leverage)
            res = self.client.create_order(**params)
        except BybitError as e:
            risk.audit(self.state_dir, "order_error", mode=self.mode, params=params, code=e.code, msg=e.msg)
            raise
        except Exception as e:  # 타임아웃 등: 거래소가 받았는지 모른다
            risk.audit(self.state_dir, "order_unknown", mode=self.mode, params=params, error=repr(e))
            raise OrderUnknown(params["orderLinkId"], e) from e
        risk.audit(self.state_dir, "order_placed", mode=self.mode, params=params, result=res,
                   ref_price=ctx.ref_price, preview=prev)
        return res

    def close_position(self, reason: str = "manual") -> dict | None:
        """열린 포지션을 시장가 reduce-only 로 전량 청산(검사 없음). 포지션 없으면 None."""
        pos = self.position()
        if pos is None:
            return None
        side = "Sell" if pos["side"] == "Buy" else "Buy"
        params = {"symbol": self.symbol, "side": side, "orderType": "Market", "qty": str(pos["size"]),
                  "reduceOnly": True, "positionIdx": 0, "orderLinkId": f"atc-close-{uuid.uuid4().hex[:14]}"}
        try:
            res = self.client.create_order(**params)
        except BybitError as e:
            risk.audit(self.state_dir, "close_error", mode=self.mode, params=params, code=e.code, msg=e.msg,
                       reason=reason)
            raise
        risk.audit(self.state_dir, "position_closed", mode=self.mode, params=params, result=res, reason=reason)
        return res

    def cancel_all(self, reason: str = "manual") -> dict:
        res = self.client.cancel_all(self.symbol)
        risk.audit(self.state_dir, "orders_cancelled", mode=self.mode, result=res, reason=reason)
        return res

    def cancel_order(self, order_id: str) -> dict:
        res = self.client.cancel_order(self.symbol, order_id)
        risk.audit(self.state_dir, "order_cancelled", mode=self.mode, order_id=order_id, result=res)
        return res

    def kill_switch(self, reason: str) -> dict:
        """정지 플래그 → 전 주문 취소 → 포지션 청산 → 잔여 포지션 확인.

        한 단계가 실패해도 다음 단계를 시도하고(정지 플래그 쓰기 실패 포함) 오류를 모두 돌려준다.
        """
        out = {"halted": False, "cancel": None, "close": None, "errors": []}

        def halt():
            risk.set_halt(self.state_dir, reason)
            out["halted"] = True

        def verify():
            if self.position() is not None:
                raise RuntimeError("청산 후에도 포지션이 남아 있다 — 거래소에서 직접 확인한다")

        for key, fn in (("halt", halt), ("cancel", lambda: self.cancel_all("kill_switch")),
                        ("close", lambda: self.close_position("kill_switch")), ("verify", verify)):
            try:
                res = fn()
                if key in ("cancel", "close"):
                    out[key] = res
            except Exception as e:  # noqa: BLE001 — 킬스위치는 끝까지 시도한다
                out["errors"].append(f"{key}: {e}")
        risk.audit(self.state_dir, "kill_switch", mode=self.mode, reason=reason, errors=out["errors"])
        return out

    def resume(self) -> None:
        risk.clear_halt(self.state_dir)
        risk.audit(self.state_dir, "resumed", mode=self.mode)

    def save_limits(self, limits: RiskLimits) -> None:
        risk.save_limits(self.limits_path, limits)
        risk.audit(self.state_dir, "limits_saved", mode=self.mode, limits=limits.__dict__)

    # 계정 점검 -----------------------------------------------------------------------------------------
    def key_check(self) -> dict:
        """API 키 권한 점검: 출금 권한(있으면 경고), 읽기 전용 여부, IP 제한."""
        info = self.client.api_key_info()
        perms = info.get("permissions") or {}
        withdraw = any("Withdraw" in (v or []) for v in perms.values()) or bool(perms.get("Withdraw"))
        ips = info.get("ips") or []
        return {"read_only": str(info.get("readOnly")) == "1", "withdraw": withdraw,
                "ip_restricted": bool(ips) and ips != ["*"], "ips": ips, "permissions": perms,
                "expires": info.get("expiredAt")}


FACTORY: Callable[[str], TradingService] | None = None  # 테스트 주입용


def make_service(mode: str | None = None) -> TradingService:
    """현재 모드(또는 지정 모드)의 서비스. 키가 없으면 공개 시세만 되는 클라이언트."""
    if FACTORY is not None:
        return FACTORY(mode or settings.get_mode())
    settings.load_env()
    mode = mode or settings.get_mode()
    k = settings.keys(mode)
    client = Bybit(BASE_URLS[mode], k.api_key if k else None, k.api_secret if k else None)
    return TradingService(client, mode, settings.agent_dir(), settings.risk_file(), settings.symbol())
