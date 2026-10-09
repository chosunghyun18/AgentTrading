"""트레이딩 — 시세·1분봉, 포지션(청산), 주문(미리보기 → 리스크 검사 → 실행), 미체결(취소)."""

import pandas as pd
import streamlit as st

from src.agent.risk import OrderRequest
from src.ui import charts, common, trading

common.setup("트레이딩")
svc = trading.service()
trading.banner(svc)
trading.show_flash()
no_keys = trading.need_keys(svc)

tk = None
with trading.guarded("시세 조회"):
    tk = svc.client.ticker(svc.symbol)
last = float(tk["lastPrice"]) if tk else 0.0
if tk:
    m = st.columns(4)
    m[0].metric("최근가", f"{last:,.1f}")
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

left, right = st.columns([1, 1])

with left, common.card("position"):
    st.subheader("포지션")
    if no_keys:
        st.caption("키 설정 후 표시")
    elif pos is None:
        st.caption("열린 포지션 없음")
    else:
        side = "롱" if pos["side"] == "Buy" else "숏"
        c = st.columns(3)
        c[0].metric("방향·수량", f"{side} {pos['size']}")
        c[1].metric("평균가", f"{float(pos['avgPrice']):,.1f}")
        c[2].metric("미실현 손익", f"{float(pos.get('unrealisedPnl') or 0):,.2f} USDT")
        st.caption(f"레버리지 {pos.get('leverage')}배 · 손절 {pos.get('stopLoss') or '없음'} · "
                   f"익절 {pos.get('takeProfit') or '없음'} · 청산가 {pos.get('liqPrice') or '-'}")
        ok = st.checkbox("전량 시장가 청산을 확인한다", key="close-confirm")
        st.button("포지션 청산", disabled=not ok, key="close-btn",
                  on_click=trading.act("청산", lambda: svc.close_position("manual"), "청산 주문을 보냈다",
                                       reset=("close-confirm",)))

with right, common.card("order"):
    st.subheader("주문")
    lim = svc.limits()
    max_lev = int(lim.max_leverage) if lim else 10
    with st.form("order-form"):
        c1, c2 = st.columns(2)
        side = c1.radio("방향", ("Buy", "Sell"), format_func={"Buy": "매수(롱)", "Sell": "매도(숏)"}.get,
                        horizontal=True, key="o-side")
        otype = c2.radio("유형", ("Market", "Limit"), format_func={"Market": "시장가", "Limit": "지정가"}.get,
                         horizontal=True, key="o-type")
        c3, c4 = st.columns(2)
        qty = c3.number_input("수량 (BTC)", min_value=0.0, value=0.001, step=0.001, format="%.3f", key="o-qty")
        lev = c4.number_input("레버리지 (배)", min_value=1, max_value=max(max_lev, 1), value=1, step=1, key="o-lev")
        price = st.number_input("지정가 (지정가 주문만)", min_value=0.0, value=round(last, 1), step=10.0, key="o-price")
        c5, c6 = st.columns(2)
        sl = c5.number_input("손절가 (필수)", min_value=0.0, value=0.0, step=10.0, key="o-sl",
                             help="매수는 기준가보다 낮게, 매도는 높게. 거래소 측 손절로 함께 건다")
        tp = c6.number_input("익절가 (0 = 없음)", min_value=0.0, value=0.0, step=10.0, key="o-tp")
        previewed = st.form_submit_button("미리보기 · 리스크 검사")
    if previewed:
        st.session_state["order-mode"] = svc.mode  # 미리보기는 그 모드에 묶는다
        st.session_state["order-req"] = OrderRequest(side=side, qty=float(qty), order_type=otype, leverage=float(lev),
                                                     stop_loss=float(sl) or None, take_profit=float(tp) or None,
                                                     price=float(price) if otype == "Limit" else None)
    if st.session_state.get("order-mode") != svc.mode:  # 모드가 바뀌면 미리보기 폐기
        st.session_state.pop("order-req", None)
    req = st.session_state.get("order-req")
    if req is not None:
        req = svc.normalize(req) if not no_keys else req
        violations, prev = ["API 키 없음"], None
        if not no_keys:
            with trading.guarded("리스크 검사"):
                violations, prev, _ = svc.review(req)
        if prev:
            p = st.columns(4)
            p[0].metric("명목가", f"{prev['notional']:,.0f} USDT")
            p[1].metric("증거금", f"{prev['margin']:,.0f} USDT")
            p[2].metric("손절 시 손실", f"{prev['loss_at_stop']:,.2f} USDT")
            p[3].metric("손익비", "-" if prev["rr"] != prev["rr"] else f"{prev['rr']:.2f}")
        if violations:
            st.error("주문 불가 — " + " / ".join(violations))
        else:
            st.success(f"검사 통과: {req.side} {req.qty:g} BTC {req.order_type} · 손절 {req.stop_loss:g}"
                       + (f" · 익절 {req.take_profit:g}" if req.take_profit else ""))
            live_ok = True
            if svc.mode == "live":
                live_ok = st.checkbox("실제 자금으로 주문함을 확인한다", key="live-confirm")
            def _place(req=req):
                res = svc.place_order(req, confirm_live=bool(st.session_state.get("live-confirm")))
                st.session_state.pop("order-req", None)
                return res

            st.button("주문 실행", type="primary", disabled=not live_ok, key="order-btn",
                      on_click=trading.act("주문", _place, lambda r: f"주문 접수: {r.get('orderId')}",
                                           reset=("live-confirm",)))

with common.card("open-orders"):
    st.subheader("미체결 주문")
    if not no_keys:
        orders = []
        with trading.guarded("미체결 조회"):
            orders = svc.client.open_orders(svc.symbol)
        if not orders:
            st.caption("없음")
        else:
            cols = ["orderId", "side", "orderType", "price", "qty", "stopLoss", "takeProfit", "orderStatus"]
            df = pd.DataFrame(orders)
            st.dataframe(df[[c for c in cols if c in df.columns]], hide_index=True, width="stretch")
            c1, c2 = st.columns([2, 1])
            oid = c1.selectbox("취소할 주문", [o["orderId"] for o in orders], key="cancel-id")
            c1.button("선택 주문 취소", key="cancel-one",
                      on_click=trading.act("취소", lambda: svc.cancel_order(oid), "취소했다"))
            c2.button("전체 취소", key="cancel-all",
                      on_click=trading.act("전체 취소", lambda: svc.cancel_all("manual"), "전체 취소했다"))
