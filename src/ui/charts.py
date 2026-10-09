"""Plotly 차트 변환 — 입력 프레임 → `go.Figure`(Streamlit 무관, 단위 테스트 대상). 색은 `theme` 토큰만 쓴다."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd
import plotly.graph_objects as go

from src.ui import theme

_LAYOUT = dict(template="plotly_white", margin=dict(l=40, r=16, t=48, b=36), height=320,
               paper_bgcolor=theme.CARD_BG, plot_bgcolor=theme.CARD_BG,
               legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
               font=dict(size=12, color=theme.TEXT))


def _fig(title: str | None = None, **kw) -> go.Figure:
    f = go.Figure()
    f.update_layout(**{**_LAYOUT, **kw}, title=dict(text=title or "", font=dict(size=14), x=0, xanchor="left", y=0.98))
    return f


def equity_fig(eq: pd.DataFrame) -> go.Figure:
    """일별 자본곡선 — 프로필(default·bybit)당 선 1개, 기준선 1.0."""
    f = _fig("검증 구간 자본곡선 (시작 = 1.0)")
    for name, g in eq.groupby("profile", sort=False):
        f.add_trace(go.Scatter(x=g["date"], y=g["equity"], mode="lines", name=str(name),
                               line=dict(color=theme.LINE_COLORS.get(str(name), theme.PRIMARY), width=2)))
    f.add_hline(y=1.0, line=dict(color=theme.INFO, dash="dot", width=1))
    return f


def fold_net_fig(folds: pd.DataFrame) -> go.Figure:
    """폴드별 검증 총 net 수익률(default·bybit 막대)."""
    f = _fig("폴드별 검증 net 수익률", barmode="group", yaxis=dict(tickformat=".0%"))
    x = [f"F{i}" for i in folds["폴드"]]
    for col, name in (("default net", "default"), ("bybit net", "bybit")):
        f.add_trace(go.Bar(x=x, y=folds[col], name=name, marker_color=theme.LINE_COLORS[name]))
    return f


def trigger_pie_fig(ratio: pd.DataFrame) -> go.Figure:
    """트리거별 게이트 통과 run 수 파이. 통과 0 이면 빈 파이 + 안내 주석."""
    f = _fig("트리거별 게이트 통과 run")
    total = int(ratio["n_pass"].sum()) if len(ratio) else 0
    if total == 0:
        f.add_annotation(text="통과 run 없음", showarrow=False, font=dict(size=14, color=theme.INFO))
        f.update_xaxes(visible=False)
        f.update_yaxes(visible=False)
        return f
    f.add_trace(go.Pie(labels=ratio["trigger"], values=ratio["n_pass"], hole=0.35, sort=False,
                       marker=dict(colors=list(theme.KPI_COLORS[:len(ratio)])),
                       customdata=ratio["n_runs"], hovertemplate="%{label}: %{value} / %{customdata} run<extra></extra>"))
    return f


def train_test_fig(folds: pd.DataFrame) -> go.Figure:
    """폴드별 학습 vs 검증 Sharpe(선택 없음 폴드는 학습 값 비움)."""
    f = _fig("폴드별 학습 vs 검증 Sharpe", barmode="group")
    x = [f"F{i}" for i in folds["폴드"]]
    f.add_trace(go.Bar(x=x, y=folds["학습 Sharpe"], name="학습", marker_color=theme.KPI_COLORS[0]))
    f.add_trace(go.Bar(x=x, y=folds["검증 Sharpe"], name="검증", marker_color=theme.KPI_COLORS[2]))
    return f


def scatter_fig(runs: pd.DataFrame, gate: Mapping) -> go.Figure:
    """run Sharpe–MDD 산점도. 게이트 통과 run 은 success 색, 나머지는 info 색. 기준선(Sharpe·MDD) 표시."""
    f = _fig("run Sharpe – MDD", height=420, xaxis=dict(title="MDD", tickformat=".0%"), yaxis=dict(title="Sharpe"))
    for passed, name, color in ((False, "그 외", theme.INFO), (True, "게이트 통과", theme.SUCCESS)):
        g = runs[(runs["gate"] == "pass") == passed]
        f.add_trace(go.Scattergl(x=g["mdd"], y=g["sharpe"], mode="markers", name=f"{name} ({len(g)})",
                                 marker=dict(color=color, size=6, opacity=0.75),
                                 customdata=g[["param_id", "n_trades"]].to_numpy(),
                                 hovertemplate="%{customdata[0]}<br>거래 %{customdata[1]}"
                                               "<br>Sharpe %{y:.3f} · MDD %{x:.1%}<extra></extra>"))
    f.add_hline(y=gate["min_sharpe"], line=dict(color=theme.DANGER, dash="dash", width=1))
    f.add_vline(x=gate["max_drawdown"], line=dict(color=theme.DANGER, dash="dash", width=1))
    return f


def candle_fig(bars: pd.DataFrame, trade: Mapping) -> go.Figure:
    """1분봉 캔들 + 진입(▲ 롱 / ▼ 숏)·청산(✕) 마커 + 손절·익절 수평선."""
    f = _fig("거래 전후 1분봉", height=460, xaxis=dict(rangeslider=dict(visible=False)))
    f.add_trace(go.Candlestick(x=bars["ts"], open=bars["open"], high=bars["high"], low=bars["low"],
                               close=bars["close"], name="XBTUSD", increasing_line_color=theme.SUCCESS,
                               decreasing_line_color=theme.DANGER))
    long = trade["side"] == "long"
    f.add_trace(go.Scatter(x=[trade["entry_ts"]], y=[trade["entry_price"]], mode="markers", name="진입",
                           marker=dict(symbol="triangle-up" if long else "triangle-down", size=14,
                                       color=theme.PRIMARY, line=dict(width=1, color="#fff"))))
    f.add_trace(go.Scatter(x=[trade["exit_ts"]], y=[trade["exit_price"]], mode="markers", name="청산",
                           marker=dict(symbol="x", size=12, color=theme.WARNING)))
    for key, color in (("stop_price", theme.DANGER), ("tp_price", theme.SUCCESS)):
        v = trade.get(key)
        if v is not None and pd.notna(v):
            f.add_hline(y=float(v), line=dict(color=color, dash="dot", width=1))
    return f


def live_candle_fig(klines: list[list[str]], position: Mapping | None = None, symbol: str = "BTCUSDT") -> go.Figure:
    """Bybit kline(최신 → 과거) 캔들 + 포지션 평균가·손절·익절 수평선."""
    df = pd.DataFrame(klines, columns=["start", "open", "high", "low", "close", "volume", "turnover"]).iloc[::-1]
    ts = pd.to_datetime(df["start"].astype("int64"), unit="ms", utc=True)
    f = _fig(f"{symbol} 1분봉", height=420, xaxis=dict(rangeslider=dict(visible=False)))
    f.add_trace(go.Candlestick(x=ts, open=df["open"].astype(float), high=df["high"].astype(float),
                               low=df["low"].astype(float), close=df["close"].astype(float), name="BTCUSDT",
                               increasing_line_color=theme.SUCCESS, decreasing_line_color=theme.DANGER))
    if position:
        for key, color, label in (("avgPrice", theme.PRIMARY, "평균가"), ("stopLoss", theme.DANGER, "손절"),
                                  ("takeProfit", theme.SUCCESS, "익절")):
            v = position.get(key)
            if v not in (None, "") and float(v) > 0:
                f.add_hline(y=float(v), line=dict(color=color, dash="dot", width=1.5),
                            annotation_text=label, annotation_position="right")
    return f
