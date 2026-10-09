"""거래 기록 — 체결 내역, 실현 손익, 콘솔 감사 로그."""

import pandas as pd
import streamlit as st

from src.agent import risk
from src.ui import common, trading

common.setup("거래 기록")
svc = trading.service()
trading.banner(svc)


def _ms(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if col in df.columns:
        df[col] = pd.to_datetime(df[col].astype("int64"), unit="ms", utc=True)
    return df


if not trading.need_keys(svc):
    c1, c2 = st.columns(2)
    with c1, common.card("execs"):
        st.subheader("최근 체결")
        with trading.guarded("체결 조회"):
            ex = pd.DataFrame(svc.client.executions(svc.symbol, 50))
            cols = ["execTime", "side", "execPrice", "execQty", "execFee", "orderType", "orderLinkId"]
            st.dataframe(_ms(ex, "execTime")[[c for c in cols if c in ex.columns]] if len(ex) else ex,
                         hide_index=True, width="stretch")
    with c2, common.card("pnl"):
        st.subheader("실현 손익")
        with trading.guarded("손익 조회"):
            cp = pd.DataFrame(svc.client.closed_pnl(svc.symbol, None, 50))
            if len(cp):
                cols = ["updatedTime", "side", "qty", "avgEntryPrice", "avgExitPrice", "closedPnl"]
                st.metric("합계(최근 50건)", f"{pd.to_numeric(cp['closedPnl']).sum():,.2f} USDT")
                st.dataframe(_ms(cp, "updatedTime")[[c for c in cols if c in cp.columns]], hide_index=True,
                             width="stretch")
            else:
                st.caption("없음")

with common.card("audit"):
    st.subheader("감사 로그 (콘솔이 한 모든 주문·취소·청산·설정 변경)")
    rows = risk.read_audit(svc.state_dir, 300)
    if rows:
        st.dataframe(pd.DataFrame([{"시각": r["ts"], "이벤트": r["event"], "모드": r.get("mode"),
                                    "내용": {k: v for k, v in r.items() if k not in ("ts", "event", "mode")}}
                                   for r in rows]), hide_index=True, width="stretch", height=420)
    else:
        st.caption("기록 없음")
