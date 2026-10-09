"""모니터 — 시세·1분봉·포지션·미체결 (읽기 전용). 수동 주문·청산·취소 기능 없음.

사용자 지시(2026-10-09): 수동 트레이딩은 하지 않는다. 비상 정지는 리스크 화면의 킬스위치로만.
"""

import pandas as pd
import streamlit as st

from src.agent import strategies as sg
from src.ui import charts, common, trading

common.setup("모니터")
svc = trading.service()
trading.banner(svc)
trading.show_flash()
now = sg.active()
st.caption(f"에이전트 적용 전략: {now.name if now else '없음 — 미가동'} · 수동 주문 없음 · 비상 정지는 리스크 화면")
no_keys = trading.need_keys(svc)

with trading.guarded("시세 조회"):
    tk = svc.client.ticker(svc.symbol)
    m = st.columns(4)
    m[0].metric("최근가", f"{float(tk['lastPrice']):,.1f}")
    m[1].metric("마크가", f"{float(tk['markPrice']):,.1f}")
    m[2].metric("24h", f"{float(tk.get('price24hPcnt') or 0):.2%}")
    m[3].metric("펀딩비", f"{float(tk.get('fundingRate') or 0):.4%}")

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
        c = st.columns(3)
        c[0].metric("방향·수량", f"{'롱' if pos['side'] == 'Buy' else '숏'} {pos['size']}")
        c[1].metric("평균가", f"{float(pos['avgPrice']):,.1f}")
        c[2].metric("미실현 손익", f"{float(pos.get('unrealisedPnl') or 0):,.2f} USDT")
        st.caption(f"레버리지 {pos.get('leverage')}배 · 손절 {pos.get('stopLoss') or '없음'} · "
                   f"익절 {pos.get('takeProfit') or '없음'} · 청산가 {pos.get('liqPrice') or '-'}")

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
