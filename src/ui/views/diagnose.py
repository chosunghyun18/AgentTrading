"""진단 — 워크포워드 미달 원인: 폴드×트리거 · 학습-검증 괴리 · 비용·펀딩 잠식 (default 프로필, 읽기 전용).

정의는 Obsidian `research/phase3-walkforward-fail-analysis.md` 와 같다. 설계: `design/user-dashboard.md` "B 단계".
"""

import streamlit as st

from src.ui import charts, common, theme
from src.ui import data as ud

common.setup("진단")
name = common.select_report()
rep = common.report(name) if name else None
if rep is None:
    common.no_report_notice()
    st.stop()

common.verdict_line(rep)
diag = common.diagnose(rep)
if diag is None:
    p = ud.diagnose_file(common.path("AT_OUT_DIR"), rep)
    funding = " --funding" if (rep.get("meta") or {}).get("funding") else ""
    st.info(f"진단 산출물이 없다 (`diagnose/{p.name}`). 다음 명령으로 만든다: "
            f"`python -m src.backtest.diagnose{funding} --jobs 4`")
    st.stop()

gate = rep["gate"]
chk = ud.diagnose_consistency(diag, rep)
selections = ud.selections(rep) if chk["ok"] else {}

with common.card("dg-source"):
    color = theme.SUCCESS if chk["ok"] else theme.WARNING
    n_folds = diag["fold"].nunique()
    st.markdown(f"{theme.badge_html(chk['reason'], color)} <span class='status-line'>"
                f"`diagnose/{ud.diagnose_file(common.path('AT_OUT_DIR'), rep).name}` · {len(diag):,}행 · "
                f"폴드 {n_folds} · run/폴드 {len(diag) // max(2 * n_folds, 1):,} · default 프로필만(bybit 없음)</span>",
                unsafe_allow_html=True)
    if not chk["ok"]:
        st.warning("진단 산출물이 판정 리포트와 맞지 않는다 — 선택 run 강조를 끈다. 진단을 다시 만든다.")

phase = st.radio("구간", list(ud.PHASE_LABELS), format_func=ud.PHASE_LABELS.get, horizontal=True, key="dg-phase")
label = ud.PHASE_LABELS[phase]
st.caption("risk_pct 1·2·5 중 **r1(=1) 고정** — 분포·비율·소진·게이트 충족은 r1, 선택 후보는 전 run"
           "(워크포워드 선택이 전 run 에서 이뤄진다). 소진 = net ≤ −99%.")

with common.card("dg-trigger"):
    st.subheader("폴드 × 트리거")
    stats = ud.trigger_fold_stats(diag, phase, gate)
    st.dataframe(stats, hide_index=True, width="stretch", key="dg-trigger-table",
                 column_config={"net>0(r1)": common.PCT, "소진(r1)": common.PCT,
                                "Sharpe 중앙(r1)": st.column_config.NumberColumn(format="%.2f"),
                                "Sharpe p90(r1)": st.column_config.NumberColumn(format="%.2f")})
    common.chart(charts.trigger_sharpe_fig(stats, label), "dg-trigger")
    st.caption(f"게이트: 거래 ≥ {gate['min_trades']} · Sharpe ≥ {gate['min_sharpe']} · MDD ≤ {gate['max_drawdown']:.0%}")

with common.card("dg-divergence"):
    st.subheader("학습-검증 괴리")
    div = ud.train_test_divergence(diag, rep, gate)
    num = st.column_config.NumberColumn(format="%.3f")
    st.dataframe(div, hide_index=True, width="stretch", key="dg-div-table",
                 column_config={c: num for c in ("Spearman(r1)", "Spearman(후보)", "선택 학습 Sharpe",
                                                 "선택 검증 Sharpe", "하락폭")}
                 | {"검증 백분위(r1)": common.PCT, "검증 백분위(후보)": common.PCT})
    st.caption("Spearman = 학습↔검증 Sharpe 순위 상관. 백분위 = 선택 run 보다 검증 Sharpe 가 낮은 run 비율.")
    folds = sorted(int(f) for f in diag["fold"].unique())
    fold = st.selectbox("폴드", folds, format_func=lambda i: f"F{i}", key="dg-fold")
    pts = ud.train_test_points(diag, fold, gate)
    common.chart(charts.divergence_fig(pts, fold, selections.get(fold)), "dg-divergence")

with common.card("dg-erosion"):
    st.subheader("비용·펀딩 잠식")
    ero = ud.cost_erosion(diag)
    st.dataframe(ero, hide_index=True, width="stretch", key="dg-erosion-table",
                 column_config={c: common.PCT for c in ("gross>0", "net>0", "gross>0 중 net≤0", "net−gross 중앙")}
                 | {"펀딩 XBT 중앙": st.column_config.NumberColumn(format="%.5f")})
    common.chart(charts.erosion_fig(ero, label), "dg-erosion")
    funded = bool(diag["total_funding_xbt"].notna().any())
    st.caption("r1 기준. \"gross>0 중 net≤0\" = 비용 전 이익 run 이 비용 후 손실로 바뀐 비율. \"전체\" = 6폴드 행 단위 합산. "
               + ("펀딩 반영." if funded else "펀딩 미반영."))
