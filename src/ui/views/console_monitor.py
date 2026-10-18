"""모니터 — 시세·1분봉·포지션·미체결 (읽기 전용). 수동 주문·청산·취소 기능 없음.

사용자 지시(2026-10-09): 수동 트레이딩은 하지 않는다. 비상 정지는 리스크 화면의 킬스위치로만.
자동 새로고침(hummingbot/dashboard 관례): 토글이 켜진 동안만 `st.fragment(run_every)` 로 아래 블록을 다시 그린다. 기본 꺼짐.
"""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from src.agent import strategies as sg
from src.ui import charts, common, theme, trading

REFRESH_SEC = 10

common.setup("모니터")
svc = trading.service()
trading.banner(svc)
trading.show_flash()
now = sg.active(sg.registry())
st.caption(f"에이전트 적용 전략: {now.name if now else '없음 — 미가동'} · 수동 주문 없음 · 비상 정지는 리스크 화면")
no_keys = trading.need_keys(svc)
auto = st.toggle(f"자동 새로고침 ({REFRESH_SEC}초, 켜진 동안 공개 시세 REST 호출)", value=False, key="mon-auto")


def _exit_plan(pos: dict) -> str:
    """포지션 청산 계획 표(평균가·손절·익절·강제 청산가) — nof0 Exit plan 관례. 없는 값은 '없음'. 읽기 전용(버튼 없음)."""
    rows = [("평균가", pos.get("avgPrice")), ("손절", pos.get("stopLoss")), ("익절", pos.get("takeProfit")),
            ("청산가(강제)", pos.get("liqPrice"))]
    body = "\n".join(f"| {k} | {float(v):,.1f} |" if v not in (None, "", "0") and float(v) > 0 else f"| {k} | 없음 |"
                     for k, v in rows)
    return "| 항목 | 값 |\n|---|---:|\n" + body


@st.fragment(run_every=REFRESH_SEC if auto else None)
def live_block() -> None:
    with trading.guarded("시세 조회"):
        tk = svc.client.ticker(svc.symbol)
        m = st.columns(4)
        m[0].metric("최근가", f"{float(tk['lastPrice']):,.1f}")
        m[1].metric("마크가", f"{float(tk['markPrice']):,.1f}")
        m[2].metric("24h", f"{float(tk.get('price24hPcnt') or 0):+.2%}")
        m[3].metric("펀딩비", f"{float(tk.get('fundingRate') or 0):+.4%}")

    pos = None
    if not no_keys:
        with trading.guarded("포지션 조회"):
            pos = svc.position()

    with common.card("live-chart"):
        with trading.guarded("차트 조회"):
            common.chart(charts.live_candle_fig(svc.client.klines(svc.symbol, "1", 180), pos, svc.symbol), "live-chart")

    left, right = st.columns(2)
    with left, common.card("position"):
        st.subheader("포지션")
        if no_keys:
            st.caption("키 설정 후 표시")
        elif pos is None:
            st.caption("열린 포지션 없음")
        else:
            side = "long" if pos["side"] == "Buy" else "short"
            upnl = float(pos.get("unrealisedPnl") or 0)
            st.markdown(f"<span class='agent-status'><b>{common.SIDE_GLYPH[side]} {pos['size']}</b> · "
                        f"레버리지 {pos.get('leverage')}배 · 미실현 {theme.pnl_html(upnl, '{:+,.2f} USDT')}</span>",
                        unsafe_allow_html=True)
            st.markdown(_exit_plan(pos))

    with right, common.card("open-orders"):
        st.subheader("미체결 주문")
        if no_keys:
            st.caption("키 설정 후 표시")
        else:
            with trading.guarded("미체결 조회"):
                orders = svc.client.open_orders(svc.symbol)
                if not orders:
                    st.caption("없음")
                else:
                    cols = ["orderId", "side", "orderType", "price", "qty", "stopLoss", "takeProfit", "orderStatus"]
                    df = pd.DataFrame(orders)
                    st.dataframe(df[[c for c in cols if c in df.columns]], hide_index=True, width="stretch")
    st.caption(f"갱신 {datetime.now(timezone.utc):%H:%M:%S} UTC · " + ("자동 새로고침 켜짐" if auto else "자동 새로고침 꺼짐"))


live_block()
