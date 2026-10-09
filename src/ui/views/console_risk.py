"""리스크 — 한도 설정, 오늘 손익·포지션 한도 사용률, 정지 상태, 킬스위치·재개."""

import streamlit as st

from src.agent import risk
from src.agent.risk import HARD_MAX_LEVERAGE, RiskLimits
from src.ui import common, theme, trading

common.setup("리스크")
svc = trading.service()
trading.banner(svc)
trading.show_flash()
lim = svc.limits()

with common.card("kill"):
    st.subheader("킬스위치")
    if svc.halted():
        st.markdown(theme.badge_html("정지 중", theme.WARNING) + f" <span class='status-line'>"
                    f"{risk.halt_reason(svc.state_dir) or ''}</span>", unsafe_allow_html=True)
        ok = st.checkbox("원인을 확인했고 신규 주문을 다시 허용한다", key="resume-ok")
        st.button("재개", disabled=not ok, key="resume-btn",
                  on_click=trading.act("재개", svc.resume, "재개했다 — 신규 주문 허용", reset=("resume-ok",)))
    else:
        st.caption("실행하면: 정지 플래그 → 전 미체결 취소 → 포지션 시장가 청산. 재개 전까지 신규 주문 거부.")
    reason = st.text_input("사유", value="수동 킬스위치", key="kill-reason")
    ok = st.checkbox("모든 주문을 취소하고 포지션을 청산한다", key="kill-ok")

    def _kill():
        out = svc.kill_switch(st.session_state.get("kill-reason") or "수동 킬스위치")
        if out["errors"]:
            raise RuntimeError("일부 단계 실패 — 거래소 화면에서 직접 확인한다: " + " / ".join(out["errors"]))

    st.button("킬스위치 실행", type="primary", disabled=not ok, key="kill-btn",
              on_click=trading.act("킬스위치", _kill, "정지·취소·청산 완료", reset=("kill-ok",)))

c1, c2 = st.columns(2)
with c1, common.card("limits"):
    st.subheader("한도 설정")
    if lim is None:
        st.warning("한도가 없어 신규 주문이 막혀 있다. 아래 값을 직접 정한다(기본값 없음).")
    with st.form("limits-form"):
        lev = st.number_input("최대 레버리지 (배)", min_value=1.0, max_value=HARD_MAX_LEVERAGE,
                              value=lim.max_leverage if lim else None, step=1.0, key="l-lev")
        pos = st.number_input("최대 포지션 규모 (USDT, 명목가)", min_value=0.0,
                              value=lim.max_position_usdt if lim else None, step=100.0, key="l-pos")
        per = st.number_input("1회 최대 손실 (USDT, 손절 도달 시)", min_value=0.0,
                              value=lim.max_loss_per_trade_usdt if lim else None, step=10.0, key="l-per")
        day = st.number_input("일 최대 손실 (USDT, UTC 하루 실현+미실현)", min_value=0.0,
                              value=lim.max_daily_loss_usdt if lim else None, step=10.0, key="l-day")
        if st.form_submit_button("저장"):
            try:
                svc.save_limits(RiskLimits(max_leverage=float(lev or 0), max_position_usdt=float(pos or 0),
                                           max_loss_per_trade_usdt=float(per or 0), max_daily_loss_usdt=float(day or 0)))
                st.success("저장했다")
                lim = svc.limits()
            except ValueError as e:
                st.error(f"저장 안 됨 — {e}")

with c2, common.card("usage"):
    st.subheader("오늘 사용률")
    if trading.need_keys(svc):
        st.stop()
    with trading.guarded("손익 조회"):
        p = svc.position()
        pnl = svc.today_pnl(p)
        st.metric("오늘 손익 (UTC, 실현+미실현)", f"{pnl:,.2f} USDT")
        if lim:
            used = max(-pnl, 0) / lim.max_daily_loss_usdt
            st.progress(min(used, 1.0), text=f"일 손실 한도 사용 {used:.0%} / {lim.max_daily_loss_usdt:,.0f} USDT")
            notional = abs(svc.position_qty(p)) * float((p or {}).get("markPrice") or 0)
            st.progress(min(notional / lim.max_position_usdt, 1.0),
                        text=f"포지션 {notional:,.0f} / {lim.max_position_usdt:,.0f} USDT")
