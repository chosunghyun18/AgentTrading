"""계정·연결 — 모드 전환(LIVE 잠금), 키 상태(뒤 4자리), 연결 테스트, 키 권한 점검, 잔고, 세팅 안내."""

import pandas as pd
import streamlit as st

from src.agent import service as agent_service
from src.agent import settings
from src.agent.bybit import BASE_URLS
from src.ui import common, trading

common.setup("계정·연결")
settings.load_env()
svc = trading.service()
trading.banner(svc)
trading.show_flash()

with common.card("mode"):
    st.subheader("운용 모드")
    unlocked = trading.live_allowed()
    rows = [{"모드": m.upper(), "엔드포인트": BASE_URLS[m],
             "API 키": (settings.keys(m).masked() if settings.keys(m) else "없음")} for m in settings.MODES]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if svc.mode == "demo":
        if not unlocked:
            st.caption(f"LIVE 잠김 — `.env` 에 `{settings.LIVE_FLAG}=1` 과 LIVE API 키가 있어야 전환할 수 있다.")
        else:
            def _to_live():
                k = agent_service.make_service("live").key_check()
                if k["withdraw"]:
                    raise PermissionError("LIVE 키에 출금 권한이 있다 — 출금 권한을 끈 키로 다시 발급한다")
                if k["read_only"]:
                    raise PermissionError("LIVE 키가 읽기 전용이다")
                settings.set_mode("live")

            ok = st.checkbox("LIVE 는 실제 자금으로 거래된다는 것을 확인한다", key="live-ack")
            st.button("LIVE 로 전환", type="primary", disabled=not ok, key="to-live",
                      on_click=trading.act("LIVE 전환", _to_live, "LIVE 로 전환했다 — 실제 자금", reset=("live-ack",)))
    else:
        st.button("DEMO 로 전환", key="to-demo",
                  on_click=trading.act("DEMO 전환", lambda: settings.set_mode("demo"), "DEMO 로 전환했다"))

if trading.need_keys(svc):
    with st.expander("계정 세팅 안내", expanded=True):
        st.markdown(trading.GUIDE)
    st.stop()

c1, c2 = st.columns(2)
with c1, common.card("conn"):
    st.subheader("연결 · 잔고")
    with trading.guarded("잔고 조회"):
        w = svc.client.wallet()
        m = st.columns(3)
        m[0].metric("총 자산", f"{float(w.get('totalEquity') or 0):,.2f} USD")
        m[1].metric("주문 가능", f"{float(w.get('totalAvailableBalance') or 0):,.2f} USD")
        m[2].metric("미실현 손익", f"{float(w.get('totalPerpUPL') or 0):,.2f} USD")
        coins = [c for c in w.get("coin", []) if float(c.get("equity") or 0) != 0]
        if coins:
            st.dataframe(pd.DataFrame(coins)[["coin", "walletBalance", "equity"]], hide_index=True, width="stretch")
        st.success("인증 연결 정상")
with c2, common.card("keycheck"):
    st.subheader("API 키 권한 점검")
    with trading.guarded("키 권한 조회"):
        k = svc.key_check()
        if k["withdraw"]:
            st.error("출금 권한이 켜져 있다 — 키를 다시 발급해 출금 권한을 끈다")
        else:
            st.success("출금 권한 없음")
        if k["read_only"]:
            st.warning("읽기 전용 키 — 주문할 수 없다")
        if not k["ip_restricted"]:
            st.warning("IP 제한 없음 — LIVE 키는 IP 제한을 권장한다")
        st.caption(f"만료 {k.get('expires') or '-'} · 허용 IP {', '.join(k['ips']) or '-'}")

with st.expander("계정 세팅 안내"):
    st.markdown(trading.GUIDE)
