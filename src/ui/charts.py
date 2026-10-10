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


# 진단(B) ----------------------------------------------------------------------------------------------

def _trigger_colors(triggers) -> dict[str, str]:
    return {t: theme.KPI_COLORS[i % len(theme.KPI_COLORS)] for i, t in enumerate(sorted(set(map(str, triggers))))}


def trigger_sharpe_fig(stats: pd.DataFrame, phase_label: str) -> go.Figure:
    """폴드별 트리거 Sharpe 중앙(r1) 막대 — 트리거당 trace 1개."""
    f = _fig(f"폴드 × 트리거 Sharpe 중앙 (r1 · {phase_label})", barmode="group")
    colors = _trigger_colors(stats["트리거"]) if len(stats) else {}
    for trig, g in stats.groupby("트리거", sort=True):
        f.add_trace(go.Bar(x=[f"F{i}" for i in g["폴드"]], y=g["Sharpe 중앙(r1)"], name=str(trig),
                           marker_color=colors[str(trig)]))
    f.add_hline(y=0, line=dict(color=theme.INFO, dash="dot", width=1))
    return f


def divergence_fig(points: pd.DataFrame, fold: int, selected: tuple[str, str] | None = None) -> go.Figure:
    """한 폴드의 학습 vs 검증 Sharpe 산점도(r1, 트리거 색) + y=x 선 + 선택 run 별표."""
    f = _fig(f"F{fold} 학습 vs 검증 Sharpe (r1)", height=420,
             xaxis=dict(title="학습 Sharpe"), yaxis=dict(title="검증 Sharpe"))
    r1 = points[points["risk_pct"] == 1.0]
    colors = _trigger_colors(r1["trigger"]) if len(r1) else {}
    for trig, g in r1.groupby("trigger", sort=True):
        f.add_trace(go.Scattergl(x=g["train_sharpe"], y=g["test_sharpe"], mode="markers", name=f"{trig} ({len(g)})",
                                 marker=dict(color=colors[str(trig)], size=6, opacity=0.7),
                                 customdata=g[["param_id"]].to_numpy(),
                                 hovertemplate="%{customdata[0]}<br>학습 %{x:.2f} · 검증 %{y:.2f}<extra></extra>"))
    vals = pd.concat([r1["train_sharpe"], r1["test_sharpe"]]).astype(float)
    vals = vals[vals.notna() & (vals.abs() != float("inf"))]
    if len(vals):
        lo, hi = float(vals.min()), float(vals.max())
        f.add_shape(type="line", x0=lo, y0=lo, x1=hi, y1=hi, line=dict(color=theme.INFO, dash="dot", width=1))
    if selected:
        s = points[(points["strategy_id"] == selected[0]) & (points["param_id"] == selected[1])]
        if len(s):
            f.add_trace(go.Scatter(x=s["train_sharpe"], y=s["test_sharpe"], mode="markers", name="선택",
                                   marker=dict(symbol="star", size=16, color=theme.DANGER,
                                               line=dict(width=1, color="#fff"))))
    return f


def erosion_fig(erosion: pd.DataFrame, phase_label: str) -> go.Figure:
    """폴드별 gross>0 vs net>0 비율(r1, 한 구간, "전체" 행 제외)."""
    e = erosion[(erosion["구간"] == phase_label) & (erosion["폴드"] != "전체")]
    f = _fig(f"비용 전후 수익 run 비율 (r1 · {phase_label})", barmode="group", yaxis=dict(tickformat=".0%"))
    for col, color in (("gross>0", theme.KPI_COLORS[1]), ("net>0", theme.KPI_COLORS[2])):
        f.add_trace(go.Bar(x=e["폴드"], y=e[col], name=col, marker_color=color))
    return f
