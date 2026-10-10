"""C1 역추세 · 보수적 메이커 체결 · 저빈도 규칙 × 1분봉 `bars_1m` → 가상 라운드트립 `roundtrips`.

입력: 한 심볼의 1분봉(`src.shared.schema.BARS_1M`), `ts` 오름차순·정확히 1분 연속(검증은 features 에 위임).
출력: `C1Run(roundtrips, skipped_min_qty, skipped_wide_stop, unfilled_limit, halted)`. `roundtrips` 는 `ROUNDTRIPS`
스키마를 통과한다(열 추가 없음 — 진입 유동성은 `entry_reason` 으로 비용 단계에서 가른다).
설계 근거(단일 기준): Obsidian `Projects/work/AgentTrading/design/phase3-c1-meanrev-maker.md`
"규칙"·"체결 모델"·"그리드"·"스키마·코드 대응". 문서와 이 파일이 다르면 문서를 따른다. v1(`synthetic.py`)은 바꾸지 않는다.

    from src.analysis.c1 import C1_GRID, C1Params, generate_c1_run
    res = generate_c1_run(bars, C1Params(n=60, k=2.0, exit="tp", stop_sigma=2.0), fill="maker")

| 규칙 | 구현 |
|---|---|
| E1 신호(봉 t 마감) | 롱 z_n ≤ −k, 숏 z_n ≥ +k. z_n·sigma_n NaN 또는 sigma_n ≤ 0 이면 신호 없음 |
| E2 손절 폭 | s = stop_sigma × sigma_n[t]. s > S_MAX(0.05) 면 건너뛰고 skipped_wide_stop += 1 |
| E3 주문 | 메이커: 지정가 L = close[t], 봉 t+1 한 봉만 유효 / 테이커: 봉 t+1 open 시장가 |
| 메이커 체결 | 롱 low[t+1] < L · 숏 high[t+1] > L(통과)일 때만 L 에 전량. 딱 닿으면 미체결·취소(unfilled_limit += 1) |
| E4 평가 | 포지션·대기 주문 없는 봉 마감에서만. 취소된 봉·손절/익절 봉·open 청산 봉 마감에서도 평가 |
| E5 사이징 | 주문 시점 자본 E·진입가 P(메이커 L, 테이커 open[t+1]). qty < 1 → skipped_min_qty += 1, 주문 없음 |
| 진입 봉 | 메이커: 손절만(갭 포함), 익절은 다음 봉부터 / 테이커: 손절·익절(통과), 갭 규칙 없음 |
| X1 손절 | 롱 P(1−s)·숏 P(1+s). 갭(롱 open ≤ 손절가)이면 open, 아니면 닿으면(low ≤) 손절가 |
| X2 익절(exit=tp) | 롱 P(1+s)·숏 P(1−s). 롱 high > 익절가·숏 low < 익절가일 때만, 갭이어도 익절가. 손절 우선 |
| X5 평균 복귀(exit=mean) | 봉 j ≥ e 마감 롱 close ≥ sma_n·숏 close ≤ sma_n → 봉 j+1 open |
| X3 시간(exit=time) | 봉 e+n−1 마감 신호 → 봉 e+n open |
| X4 구간 끝 | 마지막 봉 마감에 남은 포지션은 그 봉 close. 마지막 봉 신호는 버림 |

- 손익은 인버스 공식(XBT)·gross. 비용·펀딩은 Phase 3 비용 단계(`costs.apply_costs`·`apply_funding`).
- R4 하드 가드 `ValueError` 는 잡지 않고 전파한다(`halted` 는 자본 소진 전용). 난수 없음 — 같은 입력이면 같은 결과.
"""

from __future__ import annotations

import itertools
import math
import numbers
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.analysis.features import compute_features
from src.analysis.synthetic import (
    MAX_EXPOSURE,
    MAX_ORDER_QTY,
    ONE_MIN,
    RISK_GUARD_PCT,
    inverse_pnl,
)
from src.shared.schema import ROUNDTRIPS, empty_frame, validate_roundtrips

RULESET_VERSION = "c1"
RISK_PCT = 1.0                      # 고정(축 없음) — DSR 대표 run 정의와 같다
S_MAX = 0.05                        # E2: 손절 폭 상한(10배 격리 강제청산 거리 안쪽)
EXITS = ("mean", "tp", "time")
FILLS = ("maker", "taker")
ENTRY_REASON = {"maker": "c1_meanrev_limit", "taker": "c1_meanrev_market"}

# 설계 문서 "그리드" — 메이커 24 run(판정) + 같은 24 를 테이커(민감도)로.
GRID = {"n": (60, 240), "k": (2.0, 2.5), "exit": EXITS, "stop_sigma": (1.0, 2.0)}


def _is_pos_real(v) -> bool:
    return (isinstance(v, numbers.Real) and not isinstance(v, bool)
            and math.isfinite(v) and v > 0)


@dataclass(frozen=True)
class C1Params:
    """한 run 의 c1 파라미터(체결 모드는 `generate_c1_run(fill=)` 로 따로 받는다). 형식·범위만 검사."""

    n: int
    k: float
    exit: str
    stop_sigma: float

    def __post_init__(self):
        if not isinstance(self.n, numbers.Integral) or isinstance(self.n, bool) or self.n < 2:
            raise ValueError(f"n 은 2 이상 정수: {self.n!r}")
        if not _is_pos_real(self.k):
            raise ValueError(f"k > 0 필요: {self.k!r}")
        if self.exit not in EXITS:
            raise ValueError(f"exit 는 {EXITS} 중 하나: {self.exit!r}")
        if not _is_pos_real(self.stop_sigma):
            raise ValueError(f"stop_sigma > 0 필요: {self.stop_sigma!r}")

    @property
    def param_id(self) -> str:
        """키 알파벳 정렬 `exit=…;k=…;n=…;stop_sigma=…`, 수치는 `f"{v:g}"`."""
        return f"exit={self.exit};k={self.k:g};n={self.n:g};stop_sigma={self.stop_sigma:g}"


def strategy_id(fill: str) -> str:
    _check_fill(fill)
    return f"syn-{RULESET_VERSION}-{fill}"


def _check_fill(fill: str) -> None:
    if fill not in FILLS:
        raise ValueError(f"fill 은 {FILLS} 중 하나: {fill!r}")


def c1_grid() -> list[C1Params]:
    """설계 그리드 24 run, `param_id` 순."""
    out = [C1Params(n=n, k=k, exit=x, stop_sigma=s)
           for n, k, x, s in itertools.product(GRID["n"], GRID["k"], GRID["exit"], GRID["stop_sigma"])]
    return sorted(out, key=lambda p: p.param_id)


C1_GRID = tuple(c1_grid())


def c1_position_size(equity: float, entry_price: float, side: str, s: float,
                     risk_pct: float = RISK_PCT) -> tuple[int, bool]:
    """v1 사이징 공식에 손절 폭 비율 `s` 를 직접 넣는다(`/100` 왕복 없음) → (계약 수, 상한 절단 여부).

    롱 q_risk = r·E·P·(1−s)/s, 숏 r·E·P·(1+s)/s, qty = floor(min(q_risk, 4·E·P, q_max)). 예정 손실 > 30%·E → ValueError.
    """
    r = risk_pct / 100.0
    e, p = equity, entry_price
    if side == "long":
        q_risk = r * e * p * (1 - s) / s
        stop = p * (1 - s)
    else:
        q_risk = r * e * p * (1 + s) / s
        stop = p * (1 + s)
    q_cap = MAX_EXPOSURE * e * p
    qty = math.floor(min(q_risk, q_cap, MAX_ORDER_QTY))
    planned_loss = -inverse_pnl(side, qty, p, stop)
    if planned_loss > RISK_GUARD_PCT / 100.0 * e:
        raise ValueError(
            f"R4 하드 가드: 예정 손실 {planned_loss:.6g} XBT > 자본의 {RISK_GUARD_PCT:g}% "
            f"(equity={e:g}, s={s:g}, risk_pct={risk_pct:g})")
    return qty, bool(q_risk > min(q_cap, MAX_ORDER_QTY))


def exit_levels(side: str, entry: float, s: float, with_tp: bool) -> tuple[float, float]:
    """(손절가, 익절가). 익절 = 손절과 같은 σ 배수(1R), 없으면 NaN."""
    if side == "long":
        return entry * (1 - s), (entry * (1 + s) if with_tp else math.nan)
    return entry * (1 + s), (entry * (1 - s) if with_tp else math.nan)


def _stop_hit(side, o, h, lo, stop, gap_ok):
    """손절 판정 → 체결가 또는 None. 갭(open 이 손절가 너머)이면 open, 아니면 닿으면(≤·≥) 손절가."""
    if side == "long":
        if gap_ok and o <= stop:
            return o
        return stop if lo <= stop else None
    if gap_ok and o >= stop:
        return o
    return stop if h >= stop else None


def _tp_hit(side, h, lo, tp) -> bool:
    """익절 통과 판정(롱 high > tp · 숏 low < tp). 체결가는 항상 익절가(갭 이득 없음)."""
    if math.isnan(tp):
        return False
    return h > tp if side == "long" else lo < tp


@dataclass(frozen=True)
class C1Run:
    """run 결과: 라운드트립 + 건너뜀·미체결 집계(run 요약 JSON 에 같은 키로)."""

    roundtrips: pd.DataFrame
    skipped_min_qty: int
    skipped_wide_stop: int
    unfilled_limit: int
    halted: bool


def generate_c1_run(bars: pd.DataFrame, params: C1Params, fill: str = "maker",
                    initial_equity: float = 1.0) -> C1Run:
    """한 심볼 1분봉에 c1 규칙을 적용해 라운드트립을 만든다. 입력은 바꾸지 않는다."""
    _check_fill(fill)
    if not _is_pos_real(initial_equity):
        raise ValueError(f"initial_equity 는 > 0: {initial_equity!r}")
    feats = compute_features(bars, windows=(params.n,))  # BARS_1M·단일 심볼·1분 연속 검증
    L = len(bars)
    if L == 0:
        return C1Run(empty_frame(ROUNDTRIPS), 0, 0, 0, False)

    n, maker, with_tp = params.n, fill == "maker", params.exit == "tp"
    ts = bars["ts"].to_numpy(dtype="datetime64[ns]")
    o_ = bars["open"].to_numpy(dtype="float64")
    h_ = bars["high"].to_numpy(dtype="float64")
    l_ = bars["low"].to_numpy(dtype="float64")
    c_ = bars["close"].to_numpy(dtype="float64")
    z = feats[f"z_{n}"].to_numpy(dtype="float64")
    sig = feats[f"sigma_{n}"].to_numpy(dtype="float64")
    sma = feats[f"sma_{n}"].to_numpy(dtype="float64")
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(z) & np.isfinite(sig) & (sig > 0)
        long_sig = ok & (z <= -params.k)
        short_sig = ok & (z >= params.k)
    symbol = str(bars["symbol"].iloc[0])
    nat = np.datetime64("NaT", "ns")

    rows: list[dict] = []
    equity = float(initial_equity)
    skipped_qty = skipped_wide = unfilled = 0
    halted = False
    pos = None      # side, e, entry, qty, stop, tp, equity_before, capped, signal_j, exit_flag
    pending = None  # 메이커 지정가(봉 signal_j+1 한 봉 유효): side, L, s, qty, capped, signal_j, equity

    def close_pos(j, reason, price, exit_sig_ts):
        nonlocal equity, pos
        pnl = inverse_pnl(pos["side"], pos["qty"], pos["entry"], price)
        eb = pos["equity_before"]
        rows.append({
            "side": pos["side"], "signal_ts": ts[pos["signal_j"]], "entry_ts": ts[pos["e"]],
            "entry_price": pos["entry"], "exit_signal_ts": exit_sig_ts, "exit_ts": ts[j],
            "exit_price": float(price), "qty": pos["qty"], "stop_price": pos["stop"], "tp_price": pos["tp"],
            "exit_reason": reason, "equity_before": eb, "leverage": (pos["qty"] / pos["entry"]) / eb,
            "size_capped": pos["capped"], "gross_pnl_xbt": pnl, "gross_ret": pnl / eb,
        })
        equity = eb + pnl
        pos = None

    def open_pos(j, side, entry, s, qty, capped, signal_j, eq):
        nonlocal pos
        stop, tp = exit_levels(side, entry, s, with_tp)
        pos = {"side": side, "e": j, "entry": entry, "qty": qty, "stop": stop, "tp": tp,
               "equity_before": eq, "capped": capped, "signal_j": signal_j, "exit_flag": None}

    for j in range(L):
        can_signal = True

        # 1) 대기 지정가(메이커) — 봉 j 에서 통과하면 L 체결, 아니면 취소
        if pending is not None:
            pd_ = pending
            pending = None
            passed = l_[j] < pd_["L"] if pd_["side"] == "long" else h_[j] > pd_["L"]
            if passed:
                open_pos(j, pd_["side"], pd_["L"], pd_["s"], pd_["qty"], pd_["capped"], pd_["signal_j"],
                         pd_["equity"])
            else:
                unfilled += 1

        # 2) 보유 포지션 — 봉 안 청산 판정(순서: open 청산 → 손절 → 익절 → 구간 끝 → 봉 마감 신호)
        if pos is not None:
            entry_bar = j == pos["e"]
            exit_ = None
            if pos["exit_flag"] is not None:
                exit_ = (pos["exit_flag"], o_[j], ts[j - 1])
            else:
                price = _stop_hit(pos["side"], o_[j], h_[j], l_[j], pos["stop"],
                                  gap_ok=not entry_bar or maker)
                if price is not None:
                    exit_ = ("stop", price, nat)
                elif not (entry_bar and maker) and _tp_hit(pos["side"], h_[j], l_[j], pos["tp"]):
                    exit_ = ("take_profit", pos["tp"], nat)
                elif j == L - 1:
                    exit_ = ("end_of_data", c_[j], ts[j])
                    can_signal = False
                else:
                    if params.exit == "time" and j == pos["e"] + n - 1:
                        pos["exit_flag"] = "time"
                    elif params.exit == "mean" and (c_[j] >= sma[j] if pos["side"] == "long" else c_[j] <= sma[j]):
                        pos["exit_flag"] = "mean_revert"
            if exit_ is not None:
                close_pos(j, *exit_)
                if equity <= 0:
                    halted = True
                    break
            else:
                continue  # 보유 중 — 신호 무시(P2), 청산 신호 봉 마감 신호도 버림(E4)

        # 3) 봉 j 마감 진입 신호 평가(E1·E2·E5). 마지막 봉 신호는 버린다(E6).
        if not can_signal or j + 1 >= L:
            continue
        if long_sig[j]:
            side = "long"
        elif short_sig[j]:
            side = "short"
        else:
            continue
        s = params.stop_sigma * sig[j]
        if s > S_MAX:
            skipped_wide += 1
            continue
        entry = float(c_[j]) if maker else float(o_[j + 1])
        qty, capped = c1_position_size(equity, entry, side, s)
        if qty < 1:
            skipped_qty += 1
            continue
        if maker:
            pending = {"side": side, "L": entry, "s": s, "qty": qty, "capped": capped, "signal_j": j,
                       "equity": equity}
        else:  # 테이커: 봉 j+1 open 체결 — 다음 반복의 보유 판정이 진입 봉(손절·익절, 갭 없음)부터 한다
            open_pos(j + 1, side, entry, s, qty, capped, j, equity)

    return C1Run(_to_frame(rows, params, fill, symbol), skipped_qty, skipped_wide, unfilled, halted)


def _to_frame(rows: list[dict], params: C1Params, fill: str, symbol: str) -> pd.DataFrame:
    if not rows:
        return empty_frame(ROUNDTRIPS)
    df = pd.DataFrame(rows)
    df["strategy_id"] = strategy_id(fill)
    df["param_id"] = params.param_id
    df["trade_id"] = np.arange(len(df), dtype="int64")
    df["symbol"] = symbol
    df["entry_reason"] = ENTRY_REASON[fill]
    df["risk_pct"] = RISK_PCT
    df["notional_usd"] = df["qty"].astype("float64")
    for col in ("signal_ts", "entry_ts", "exit_signal_ts", "exit_ts"):
        df[col] = pd.to_datetime(df[col].to_numpy(dtype="datetime64[ns]")).tz_localize("UTC")
    df["holding_min"] = (df["exit_ts"] - df["entry_ts"]) / ONE_MIN
    df = df[ROUNDTRIPS.column_names].astype(ROUNDTRIPS.dtypes)
    return validate_roundtrips(df)
