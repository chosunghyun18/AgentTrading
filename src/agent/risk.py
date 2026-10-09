"""리스크 한도·주문 사전 검사·정지(HALT) 플래그·감사 로그 — 거래소 호출 없는 순수 로직.

신규 주문은 `check_order` 위반이 하나라도 있으면 거부한다. 청산(reduce-only)·취소·킬스위치는 검사하지 않는다
(위험을 줄이는 방향). 한도는 기본값이 없다 — 사람이 정하기 전에는 신규 주문 거부.
설계: Obsidian `design/trading-console.md` "리스크 사전 검사".
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

HARD_MAX_LEVERAGE = 10.0  # Spec 3.1절 원칙 위반 기준(레버리지 > 10배)
SIDES = ("Buy", "Sell")
ORDER_TYPES = ("Market", "Limit")
SECRET_FIELDS = ("api_key", "api_secret", "sign", "X-BAPI-SIGN", "X-BAPI-API-KEY")


@dataclass(frozen=True)
class RiskLimits:
    max_leverage: float           # 배
    max_position_usdt: float      # 주문 후 포지션 명목가 상한
    max_loss_per_trade_usdt: float  # 손절 도달 시 예상 손실 상한
    max_daily_loss_usdt: float    # 오늘(UTC) 실현+미실현 손실 상한

    def __post_init__(self):
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                raise ValueError(f"{f.name} 는 0보다 큰 수여야 한다: {v!r}")
        if self.max_leverage > HARD_MAX_LEVERAGE:
            raise ValueError(f"max_leverage 는 {HARD_MAX_LEVERAGE:g}배 이하(Spec 원칙): {self.max_leverage}")


def load_limits(path: Path) -> RiskLimits | None:
    """없거나 형식이 틀리면 None(→ 신규 주문 거부)."""
    try:
        d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return RiskLimits(**{f.name: float(d[f.name]) for f in fields(RiskLimits)})
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        return None


def save_limits(path: Path, limits: RiskLimits) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("# 트레이딩 콘솔 리스크 한도(USDT·배) — 커밋 금지\n" + yaml.safe_dump(asdict(limits), sort_keys=False),
                   encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class Rules:
    """거래소 상품 규칙(instruments-info)."""
    qty_step: float
    min_qty: float
    max_mkt_qty: float
    min_notional: float
    tick: float

    @classmethod
    def from_instrument(cls, inst: dict) -> "Rules":
        lot, pf = inst["lotSizeFilter"], inst["priceFilter"]
        return cls(float(lot["qtyStep"]), float(lot["minOrderQty"]), float(lot.get("maxMktOrderQty") or lot["maxOrderQty"]),
                   float(lot.get("minNotionalValue") or 0), float(pf["tickSize"]))


def _step(v: float, step: float, rounding) -> float:
    s = Decimal(str(step))
    return float((Decimal(str(v)) / s).to_integral_value(rounding=rounding) * s)


def num_str(x: float) -> str:
    """거래소 전송용 숫자 문자열 — 지수 표기·유효숫자 손실 없음(`f"{x:g}"` 는 123456.7 → "123457")."""
    d = Decimal(str(x)).normalize()
    return format(d, "f")


def round_qty(qty: float, step: float) -> float:
    """수량은 내림(요청보다 크게 주문하지 않는다)."""
    return _step(qty, step, ROUND_DOWN)


def round_price(price: float, tick: float) -> float:
    return _step(price, tick, ROUND_HALF_UP)


@dataclass(frozen=True)
class OrderRequest:
    side: str              # Buy | Sell
    qty: float             # BTC
    order_type: str        # Market | Limit
    leverage: float
    stop_loss: float | None
    take_profit: float | None = None
    price: float | None = None  # Limit 일 때만


@dataclass(frozen=True)
class Context:
    mode: str              # demo | live
    live_unlocked: bool
    halted: bool
    limits: RiskLimits | None
    ref_price: float       # 지정가면 그 가격, 시장가면 최근가
    position_qty: float    # 부호 있는 현재 포지션(롱 +, 숏 −)
    today_pnl: float       # 오늘(UTC) 실현 + 현재 미실현, USDT
    rules: Rules
    position_avg: float = 0.0      # 현재 포지션 평균가
    open_order_qty: float = 0.0    # 미체결 신규(비 reduce-only) 주문 수량 합(BTC, 절댓값)
    available: float = float("inf")  # 주문 가능 잔고(USDT)


def signed(side: str, qty: float) -> float:
    return qty if side == "Buy" else -qty


def _finite(*xs) -> bool:
    return all(x is None or (isinstance(x, (int, float)) and math.isfinite(x)) for x in xs)


def preview(req: OrderRequest, ctx: Context) -> dict:
    """주문 미리보기 수치(위반 여부와 무관).

    - `position_after`: 기존 포지션 + 미체결 신규 주문 + 이 주문이 모두 체결된 최악 명목가.
    - `loss_at_stop`: 손절은 `tpslMode=Full` 로 포지션 전체에 걸리므로 기존 포지션(평균가 기준)과
      이 주문(기준가 기준)을 합친 손절 시 손실.
    """
    notional = req.qty * ctx.ref_price
    after = (abs(ctx.position_qty) + ctx.open_order_qty + req.qty) * ctx.ref_price
    loss = float("nan")
    if req.stop_loss:
        sgn = 1 if req.side == "Buy" else -1
        existing = abs(ctx.position_qty) * sgn * ((ctx.position_avg or ctx.ref_price) - req.stop_loss)
        loss = max(existing + req.qty * sgn * (ctx.ref_price - req.stop_loss), 0.0)
    gain = req.qty * abs(req.take_profit - ctx.ref_price) if req.take_profit else float("nan")
    new_loss = req.qty * abs(ctx.ref_price - req.stop_loss) if req.stop_loss else float("nan")
    return {"notional": notional, "margin": notional / req.leverage if req.leverage > 0 else float("nan"),
            "position_after": after, "loss_at_stop": loss, "gain_at_tp": gain,
            "rr": gain / new_loss if new_loss and new_loss == new_loss and gain == gain else float("nan")}


LIQ_BUFFER = 0.8  # 손절 거리 < (1/레버리지) × 0.8 — 유지증거금 몫을 남겨 청산보다 손절이 먼저 닿게


def check_order(req: OrderRequest, ctx: Context) -> list[str]:
    """신규 주문 사전 검사 위반 목록(빈 목록 = 통과)."""
    v: list[str] = []
    if ctx.halted:
        v.append("정지(HALT) 상태 — 리스크 화면에서 재개해야 신규 주문 가능")
    lim = ctx.limits
    if lim is None:
        v.append("리스크 한도 미설정 — 리스크 화면에서 먼저 정한다")
    if ctx.mode == "live" and not ctx.live_unlocked:
        v.append("LIVE 잠금 해제 안 됨(.env AT_LIVE_ENABLED=1·LIVE 키)")
    if not _finite(req.qty, req.leverage, req.stop_loss, req.take_profit, req.price, ctx.ref_price):
        v.append("숫자가 아닌 값(NaN·무한대)이 있다")
        return v
    if req.side not in SIDES:
        v.append(f"방향은 {SIDES}")
        return v
    if req.order_type not in ORDER_TYPES:
        v.append(f"주문 유형은 {ORDER_TYPES}")
    if req.order_type == "Limit" and not (req.price and req.price > 0):
        v.append("지정가 주문은 가격이 필요하다")
    if not (ctx.ref_price > 0):
        v.append("기준가를 알 수 없다(시세 조회 실패)")
        return v
    if ctx.position_qty and (ctx.position_qty > 0) != (req.side == "Buy"):
        v.append("반대 방향 포지션이 있다 — 줄이거나 뒤집으려면 먼저 청산한다")
    r = ctx.rules
    if not (req.qty > 0) or req.qty < r.min_qty:
        v.append(f"수량은 최소 {r.min_qty:g} BTC")
    if req.order_type == "Market" and req.qty > r.max_mkt_qty:
        v.append(f"시장가 최대 수량 {r.max_mkt_qty:g} BTC 초과")
    if req.qty * ctx.ref_price < r.min_notional:
        v.append(f"주문 명목가는 최소 {r.min_notional:g} USDT")
    if not req.stop_loss:
        v.append("손절가 필수")
    elif req.side == "Buy" and req.stop_loss >= ctx.ref_price:
        v.append("매수 손절가는 기준가보다 낮아야 한다")
    elif req.side == "Sell" and req.stop_loss <= ctx.ref_price:
        v.append("매도 손절가는 기준가보다 높아야 한다")
    elif req.leverage > 0 and abs(ctx.ref_price - req.stop_loss) / ctx.ref_price >= LIQ_BUFFER / req.leverage:
        v.append(f"손절 거리 {abs(ctx.ref_price - req.stop_loss) / ctx.ref_price:.1%} 가 {req.leverage:g}배의 "
                 f"추정 청산 거리(≈{LIQ_BUFFER / req.leverage:.1%}) 이상 — 손절 전에 청산될 수 있다")
    if req.take_profit:
        if (req.side == "Buy" and req.take_profit <= ctx.ref_price) or \
                (req.side == "Sell" and req.take_profit >= ctx.ref_price):
            v.append("익절가가 기준가의 잘못된 쪽에 있다")
    if not (0 < req.leverage <= HARD_MAX_LEVERAGE):
        v.append(f"레버리지는 0 초과 {HARD_MAX_LEVERAGE:g}배 이하")
    p = preview(req, ctx)
    if req.leverage > 0 and p["margin"] > ctx.available:
        v.append(f"필요 증거금 {p['margin']:,.2f} USDT > 주문 가능 {ctx.available:,.2f}")
    if lim is not None:
        if req.leverage > lim.max_leverage:
            v.append(f"레버리지 {req.leverage:g}배 > 한도 {lim.max_leverage:g}배")
        if p["position_after"] > lim.max_position_usdt:
            v.append(f"주문 후 포지션(미체결 포함) {p['position_after']:,.0f} USDT > 한도 {lim.max_position_usdt:,.0f}")
        if req.stop_loss and p["loss_at_stop"] > lim.max_loss_per_trade_usdt:
            v.append(f"손절 시 손실(포지션 전체) {p['loss_at_stop']:,.2f} USDT > 1회 한도 {lim.max_loss_per_trade_usdt:,.2f}")
        if ctx.today_pnl <= -lim.max_daily_loss_usdt:
            v.append(f"오늘 손익 {ctx.today_pnl:,.2f} USDT — 일 손실 한도 {lim.max_daily_loss_usdt:,.2f} 도달")
        elif req.stop_loss and ctx.today_pnl - p["loss_at_stop"] < -lim.max_daily_loss_usdt:
            v.append(f"손절되면 오늘 손익 {ctx.today_pnl - p['loss_at_stop']:,.2f} USDT — "
                     f"일 손실 한도 {lim.max_daily_loss_usdt:,.2f} 초과")
    return v


# 정지(HALT) --------------------------------------------------------------------------------------------

def halt_path(state_dir: Path) -> Path:
    return Path(state_dir) / "HALT"


def is_halted(state_dir: Path) -> bool:
    return halt_path(state_dir).exists()


def halt_reason(state_dir: Path) -> str | None:
    try:
        return halt_path(state_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def set_halt(state_dir: Path, reason: str) -> None:
    p = halt_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{_now()} {reason}\n", encoding="utf-8")


def clear_halt(state_dir: Path) -> None:
    halt_path(state_dir).unlink(missing_ok=True)


# 감사 로그 ----------------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: ("***" if k in SECRET_FIELDS else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(x) for x in obj]
    return obj


def audit_path(state_dir: Path) -> Path:
    return Path(state_dir) / "audit.jsonl"


def audit(state_dir: Path, event: str, **fields_) -> dict:
    """append-only 1행. 키·서명 필드는 가린다."""
    rec = {"ts": _now(), "event": event, **_scrub(fields_)}
    p = audit_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return rec


def read_audit(state_dir: Path, limit: int = 200) -> list[dict]:
    try:
        lines = audit_path(state_dir).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:][::-1]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
