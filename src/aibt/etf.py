"""ETF 일봉 엔진 — `Spec` 을 결정적으로 실행한다. 쓰기 없음.

수치 정의(설계 문서 "수치 정의"):
- 가격 = `prices_daily.adj_close`(배당 재투자). 현금 이자 0.
- 신호(이동평균·낙폭)는 종가로 판정하고 **다음 거래일 종가**에 체결. 월 적립·리밸런싱은 그 달 첫 거래일 종가(같은 날).
- 여러 종목이면 **공통 시작일**(가장 늦게 시작하는 종목)부터. 벤치마크도 같은 구간.
- 지표는 **단위가치 지수(TWR, 시작 1.0)** 로 — 일 수익률 = (오늘 평가액 − 오늘 납입) / 어제 평가액 − 1. 적립금이 낙폭·CAGR 을
  가리지 않는다. 총납입 대비 배수는 따로 준다.

    prices = load_prices(url, spec.symbols)      # DataFrame[date × symbol] adj_close
    res = run(spec, prices)                       # Result
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.aibt.spec import Spec
from src.marketdata.db import engine

TRADING_DAYS = 252


def catalog(url: str) -> dict[str, tuple[date, date]]:
    """{종목: (첫날, 끝날)} — `prices_daily` 에 있는 것만."""
    q = text("SELECT symbol, MIN(date) AS first, MAX(date) AS last FROM prices_daily GROUP BY symbol ORDER BY symbol")
    with engine(url).connect() as con:
        rows = con.execute(q).all()
    return {r[0]: (pd.Timestamp(r[1]).date(), pd.Timestamp(r[2]).date()) for r in rows}


def load_prices(url: str, symbols: list[str]) -> pd.DataFrame:
    """종목들의 adj_close 를 날짜 × 종목 프레임으로. 없는 날은 NaN."""
    if not symbols:
        raise ValueError("종목이 없다")
    q = text("SELECT symbol, date, adj_close FROM prices_daily WHERE symbol IN :syms ORDER BY date").bindparams(
        __import__("sqlalchemy").bindparam("syms", expanding=True))
    with engine(url).connect() as con:
        df = pd.read_sql(q, con, params={"syms": list(symbols)})
    if df.empty:
        raise ValueError(f"가격 데이터가 없다: {symbols}")
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot(index="date", columns="symbol", values="adj_close").sort_index().astype("float64")


@dataclass
class Result:
    spec: Spec
    equity: pd.Series                   # 평가액
    contributed: pd.Series              # 누적 납입
    unit: pd.Series                     # 단위가치 지수(시작 1.0)
    benchmark_unit: pd.Series | None
    trades: pd.DataFrame                # date · action · symbol · amount · reason
    metrics: dict
    yearly: pd.DataFrame                # year · 전략 · (벤치마크)
    notes: list[str] = field(default_factory=list)


class _Book:
    """현금 + 종목별 주수. 체결은 그날 종가."""

    def __init__(self, px: pd.DataFrame):
        self.px = px.to_numpy()
        self.cols = {c: j for j, c in enumerate(px.columns)}
        self.cash = 0.0
        self.shares = np.zeros(len(px.columns))
        self.trades: list[dict] = []

    def price(self, i: int, sym: str) -> float:
        return float(self.px[i, self.cols[sym]])

    def value(self, i: int) -> float:
        return self.cash + float(self.shares @ self.px[i])

    def buy(self, i: int, dt, sym: str, amount: float, reason: str) -> float:
        amount = min(amount, self.cash)
        if amount <= 1e-9:
            return 0.0
        self.shares[self.cols[sym]] += amount / self.price(i, sym)
        self.cash -= amount
        self.trades.append({"date": dt, "action": "매수", "symbol": sym, "amount": amount, "reason": reason})
        return amount

    def sell_all(self, i: int, dt, sym: str, reason: str) -> float:
        j = self.cols[sym]
        amount = float(self.shares[j]) * self.price(i, sym)
        if amount <= 1e-9:
            return 0.0
        self.shares[j] = 0.0
        self.cash += amount
        self.trades.append({"date": dt, "action": "매도", "symbol": sym, "amount": amount, "reason": reason})
        return amount

    def buy_weights(self, i: int, dt, weights: dict[str, float], amount: float, reason: str) -> None:
        for sym, w in weights.items():
            self.buy(i, dt, sym, amount * w, reason)

    def rebalance(self, i: int, dt, weights: dict[str, float], reason: str) -> None:
        total = self.value(i)
        diffs = {s: total * w - float(self.shares[self.cols[s]]) * self.price(i, s) for s, w in weights.items()}
        for s, d in diffs.items():  # 먼저 팔아 현금을 만든다
            if d < -1e-9:
                j = self.cols[s]
                self.shares[j] -= -d / self.price(i, s)
                self.cash += -d
                self.trades.append({"date": dt, "action": "매도", "symbol": s, "amount": -d, "reason": reason})
        for s, d in diffs.items():
            if d > 1e-9:
                self.buy(i, dt, s, d, reason)


def _period_firsts(idx: pd.DatetimeIndex, period: str) -> np.ndarray:
    """그 기간의 첫 거래일(불리언). 첫 행은 포함하지 않는다(시작일 처리는 따로)."""
    if period == "monthly":
        key = idx.year * 12 + idx.month
    elif period == "quarterly":
        key = idx.year * 4 + (idx.month - 1) // 3
    else:
        key = idx.year
    out = np.zeros(len(idx), dtype=bool)
    out[1:] = np.asarray(key[1:]) != np.asarray(key[:-1])
    return out


def _month_lasts(idx: pd.DatetimeIndex) -> np.ndarray:
    key = idx.year * 12 + idx.month
    out = np.zeros(len(idx), dtype=bool)
    out[:-1] = np.asarray(key[:-1]) != np.asarray(key[1:])
    out[-1] = True
    return out


def _window(spec: Spec, prices: pd.DataFrame, notes: list[str]) -> tuple[pd.DataFrame, pd.Series | None]:
    """공통 구간으로 자른 가격 + (ma_filter 면) 이동평균. 잘린 사유는 notes 에."""
    missing = [s for s in spec.symbols if s not in prices.columns]
    if missing:
        raise ValueError(f"가격 데이터에 없는 종목: {missing}")
    px = prices[spec.symbols]
    first = max(px[s].first_valid_index() for s in spec.symbols)
    last = min(px[s].last_valid_index() for s in spec.symbols)
    ma = None
    if spec.rule == "ma_filter":
        ma = prices[spec.signal_symbol].rolling(spec.ma_window).mean()
        first_ma = ma.first_valid_index()
        if first_ma is not None and first_ma > first:
            notes.append(f"{spec.signal_symbol} {spec.ma_window}일선이 처음 계산되는 {first_ma.date()} 부터 시작한다")
            first = first_ma
    start = pd.Timestamp(spec.start) if spec.start else first
    end = pd.Timestamp(spec.end) if spec.end else last
    if start < first:
        notes.append(f"요청 시작일 {start.date()} 이전 데이터가 없어 {first.date()} 부터 시작한다(공통 시작일)")
        start = first
    if end > last:
        notes.append(f"요청 끝일 {end.date()} 이후 데이터가 없어 {last.date()} 에 끝난다")
        end = last
    win = px.loc[start:end].dropna()
    if len(win) < 2:
        raise ValueError(f"구간 {start.date()}~{end.date()} 에 데이터가 2일 미만이다")
    return win, (ma.reindex(win.index) if ma is not None else None)


def _simulate(spec: Spec, px: pd.DataFrame, ma: pd.Series | None) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """평가액·당일 납입 배열·거래 목록. 규칙별 분기는 여기 한 곳."""
    idx = px.index
    n = len(idx)
    book = _Book(px)
    month_first = _period_firsts(idx, "monthly")
    period_first = _period_firsts(idx, spec.rebalance_period) if spec.rule == "rebalance" else month_first
    eom = _month_lasts(idx)
    equity = np.empty(n)
    flows = np.zeros(n)
    C = spec.monthly_contribution
    w = spec.weights
    book.cash = spec.initial_cash

    # 규칙별 상태
    state = ""            # ma_filter: 현재 보유 종목("" = 현금)
    pending_switch = None  # ma_filter: 다음 거래일에 바꿀 종목
    pending_stages: list = []
    rung, pool, deployed = 0, None, 0.0
    if spec.rule == "drawdown_trigger":
        ref = px[spec.ref_symbol]
        if spec.reset_years == 0:
            ref_high = ref.cummax()
        else:
            ref_high = ref.rolling(f"{spec.reset_years * 365}D", min_periods=1).max()
        ref_v, high_v = ref.to_numpy(), ref_high.to_numpy()
        thresholds = [(1 - s.drawdown_pct / 100, s.cumulative_fraction) for s in spec.stages]
    if spec.rule == "ma_filter":
        sig = px[spec.signal_symbol].to_numpy()
        ma_v = ma.to_numpy()

    def target_of(i: int) -> str:
        return spec.hold_symbol if sig[i] > ma_v[i] else spec.else_symbol

    for i in range(n):
        dt = idx[i]
        if i == 0:  # 시작일: 초기 자금 배치
            if spec.rule in ("buy_and_hold", "dca", "rebalance"):
                book.buy_weights(i, dt, w, book.cash, "시작")
            elif spec.rule == "ma_filter":
                state = target_of(i)
                if state:
                    book.buy(i, dt, state, book.cash, "시작")
            else:
                book.buy_weights(i, dt, w, book.cash * (1 - spec.reserve_fraction), "시작(기본 보유)")
        else:
            # 1) 월 적립
            if C > 0 and month_first[i]:
                book.cash += C
                flows[i] = C
                if spec.rule in ("buy_and_hold", "dca"):
                    book.buy_weights(i, dt, w, C, "월 적립")
                elif spec.rule == "ma_filter":
                    if state:
                        book.buy(i, dt, state, C, "월 적립")
                elif spec.rule == "drawdown_trigger":
                    book.buy_weights(i, dt, w, C * (1 - spec.reserve_fraction), "월 적립(기본 보유)")
                # rebalance 는 아래 리밸런싱이 현금을 함께 배치한다
            # 2) 전날 신호 체결
            if spec.rule == "ma_filter" and pending_switch is not None:
                if state:
                    book.sell_all(i, dt, state, "이동평균 전환")
                state = pending_switch
                if state:
                    book.buy(i, dt, state, book.cash, "이동평균 전환")
                pending_switch = None
            if spec.rule == "drawdown_trigger" and pending_stages:
                for stage_no, cum in pending_stages:
                    if pool is None:
                        pool = book.cash
                    spend = max(0.0, cum * pool - deployed)
                    deployed += book.buy(i, dt, spec.vehicle, spend, f"{stage_no}단계 트리거")
                pending_stages = []
            # 3) 정기 리밸런싱(그날 종가)
            if spec.rule == "rebalance" and period_first[i]:
                book.rebalance(i, dt, w, "리밸런싱")
            elif spec.rule == "rebalance" and C > 0 and month_first[i]:
                book.buy_weights(i, dt, w, C, "월 적립")
        # 4) 오늘 종가로 신호 판정 → 다음 거래일
        if spec.rule == "ma_filter":
            t = target_of(i)
            if t != state and pending_switch is None and i < n - 1:
                pending_switch = t
            elif t == state:
                pending_switch = None
        if spec.rule == "drawdown_trigger":
            if ref_v[i] >= high_v[i]:  # 기준 신고가 → 사이클 리셋
                rung, pool, deployed = 0, None, 0.0
            dd = ref_v[i] / high_v[i]
            armed = 0
            for k, (th, cum) in enumerate(thresholds, 1):
                if dd <= th and (spec.check == "daily" or eom[i]) and k > rung:
                    armed = k
            if armed and i < n - 1:
                pending_stages = [(k, thresholds[k - 1][1]) for k in range(rung + 1, armed + 1)]
                rung = armed
        equity[i] = book.value(i)
    return equity, flows, book.trades


def _unit(equity: np.ndarray, flows: np.ndarray) -> np.ndarray:
    r = np.zeros(len(equity))
    r[1:] = (equity[1:] - flows[1:]) / equity[:-1] - 1
    return np.cumprod(1 + r)


def _metrics(unit: pd.Series, equity: pd.Series, contributed: pd.Series) -> dict:
    r = unit.pct_change().dropna()
    years = (unit.index[-1] - unit.index[0]).days / 365.25
    dd = unit / unit.cummax() - 1
    return {
        "final_equity": float(equity.iloc[-1]),
        "contributed": float(contributed.iloc[-1]),
        "multiple": float(equity.iloc[-1] / contributed.iloc[-1]),
        "unit_multiple": float(unit.iloc[-1]),
        "cagr": float(unit.iloc[-1] ** (1 / years) - 1) if years >= 0.5 else float("nan"),
        "mdd": float(dd.min()),
        "mdd_date": dd.idxmin().date(),
        "vol": float(r.std(ddof=0) * np.sqrt(TRADING_DAYS)) if len(r) > 1 else float("nan"),
        "years": float(years),
        "start": unit.index[0].date(),
        "end": unit.index[-1].date(),
    }


def _yearly(unit: pd.Series) -> pd.Series:
    r = unit.pct_change().fillna(0.0)
    return (1 + r).groupby(r.index.year).prod() - 1


def run(spec: Spec, prices: pd.DataFrame) -> Result:
    notes: list[str] = []
    px, ma = _window(spec, prices, notes)
    equity, flows, trades = _simulate(spec, px, ma)
    idx = px.index
    eq = pd.Series(equity, idx, name="equity")
    contributed = pd.Series(spec.initial_cash + np.cumsum(flows), idx, name="contributed")
    unit = pd.Series(_unit(equity, flows), idx, name="unit")
    metrics = _metrics(unit, eq, contributed)
    metrics["n_trades"] = len(trades)
    metrics["n_buys"] = sum(1 for t in trades if t["action"] == "매수")
    yearly = _yearly(unit).rename("전략").to_frame()
    bench_unit = None
    if spec.benchmark:
        b_spec = Spec(title="", rule="buy_and_hold", start=None, end=None, initial_cash=spec.initial_cash,
                      monthly_contribution=spec.monthly_contribution, benchmark="", weights={spec.benchmark: 1.0})
        b_eq, b_flows, _ = _simulate(b_spec, px[[spec.benchmark]], None)
        bench_unit = pd.Series(_unit(b_eq, b_flows), idx, name="benchmark")
        bm = _metrics(bench_unit, pd.Series(b_eq, idx), contributed)
        metrics["benchmark"] = {k: bm[k] for k in ("multiple", "unit_multiple", "cagr", "mdd", "vol")}
        yearly[spec.benchmark] = _yearly(bench_unit)
    trades_df = pd.DataFrame(trades, columns=["date", "action", "symbol", "amount", "reason"])
    return Result(spec, eq, contributed, unit, bench_unit, trades_df, metrics, yearly, notes)
