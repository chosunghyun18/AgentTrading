"""워크포워드 — 폴드 표 · 검증 자본곡선 · 폴드별 net/학습-검증 · DSR · 메타."""

import pandas as pd
import streamlit as st

from src.ui import charts, common
from src.ui import data as ud

common.setup("워크포워드")
name = common.select_report()
rep = common.report(name) if name else None
if rep is None:
    common.no_report_notice()
    st.stop()

common.verdict_line(rep)
profile = st.radio("프로필", ud.PROFILES, horizontal=True, key="wf-profile",
                   help="default = 판정, bybit = 수수료 민감도")
common.kpi_row(ud.gate_kpis(rep, profile))

folds = ud.fold_rows(rep)
with common.card("folds"):
    st.subheader("폴드")
    st.dataframe(folds, hide_index=True, width="stretch", key="fold-table",
                 column_config={"default net": common.PCT, "bybit net": common.PCT, "검증 MDD": common.PCT,
                                "학습 Sharpe": st.column_config.NumberColumn(format="%.3f"),
                                "검증 Sharpe": st.column_config.NumberColumn(format="%.3f")})

with common.card("wf-equity"):
    eq = common.equity(name)
    if eq is None:
        common.no_equity_notice(name)
    else:
        if ud.equity_stale(common.path("AT_OUT_DIR"), name):
            common.stale_notice(name)
        common.chart(charts.equity_fig(eq), "wf-equity")

c1, c2 = st.columns(2)
with c1, common.card("wf-fold-net"):
    common.chart(charts.fold_net_fig(folds), "wf-fold-net")
with c2, common.card("wf-train-test"):
    common.chart(charts.train_test_fig(folds), "wf-train-test")

d1, d2 = st.columns(2)
with d1, common.card("dsr"):
    st.subheader("이어 붙인 검증 곡선 · DSR")
    dsr = rep.get("dsr", {})
    rows = []
    for p in ud.PROFILES:
        s = rep["stitched"][p]
        rows.append({"프로필": p, "거래": s["n_trades"], "Sharpe": s["sharpe"], "MDD": s["mdd"],
                     "총 net": s["total_net_ret"], "일 수": s["n_days"], "DSR": dsr.get(p), "판정": s["gate"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", key="dsr-table",
                 column_config={"MDD": common.PCT, "총 net": common.PCT})
    st.caption(f"SR0 {dsr.get('sr0', float('nan')):.4f} · V {dsr.get('var_sr', float('nan')):.4f} "
               f"(risk_pct=1 대표 run {dsr.get('n_var_runs')}) · 시행 수 N={rep['n_trials']}")
with d2, common.card("meta"):
    st.subheader("실행 조건")
    meta = rep.get("meta", {})
    gate = rep["gate"]
    st.markdown(f"- 종목 `{meta.get('symbol')}` · 규칙 `{meta.get('ruleset_version')}` · run {meta.get('n_runs')}\n"
                f"- 게이트: 거래 ≥ {gate['min_trades']} · Sharpe ≥ {gate['min_sharpe']} · "
                f"MDD ≤ {gate['max_drawdown']:.0%} · DSR ≥ {gate.get('min_dsr')}\n"
                f"- 펀딩: {'반영 (' + str(meta['funding'].get('n_settlements')) + '회 정산)' if meta.get('funding') else '미반영'}")
    sel = (rep.get("full_sample") or {}).get("selected")
    if sel:
        st.markdown(f"- 표본 전체 선택(OOS 후보): `{sel['param_id']}` · Sharpe {sel['sharpe']:.3f}")
    with st.expander("수수료·그리드 원본"):
        st.json({"fee": meta.get("fee"), "grid": meta.get("grid")})
