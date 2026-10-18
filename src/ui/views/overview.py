"""대시보드(첫 화면) — 에이전트 상태 한 줄 · 게이트 KPI 4 · 검증 자본곡선+낙폭 · 차트 3열 · 최근 거래/사람 할 일/Phase 진행.

배치 근거: Obsidian `design/ui-ux-v2-benchmark.md`(OctoBot 첫 화면·FreqUI BotStatus·Vibe-Trading EquityChart). 읽기 전용.
"""

import pandas as pd
import streamlit as st

from src.ui import charts, common, dashboard
from src.ui import data as ud

common.setup("대시보드")
name = common.select_report()
common.agent_status_card()
rep = common.report(name) if name else None
if rep is None:
    dashboard.empty_state()
    st.stop()

dashboard.context(name, rep)
common.verdict_line(rep)
dashboard.next_step(rep)
profile = st.radio("비용 프로필", ud.PROFILES, format_func=dashboard.PROFILE_LABELS.get,
                   horizontal=True, key="overview-profile")
common.kpi_row(ud.gate_kpis(rep, profile))

eq = common.equity(name)
left, right = st.columns([2, 1])
with left, common.card("equity"):
    st.subheader("검증 성과")
    dashboard.performance(rep, profile)
    if eq is None:
        common.no_equity_notice(name)
    else:
        if ud.equity_stale(common.path("AT_OUT_DIR"), name):
            common.stale_notice(name)
        compare = st.toggle("비용 프로필 비교", value=True, key="overview-compare")
        curve = eq if compare else eq[eq["profile"] == profile]
        common.chart(charts.equity_fig(curve), "equity")
        st.caption("시작 자본 1.0 · 선택 기간은 차트에만 적용 · 요약 지표는 전체 검증 표본 기준")
with right, common.card("decision"):
    dashboard.decision(rep, profile)

folds = ud.fold_rows(rep)
with st.expander("검증 분석 · 폴드 수익률 / 트리거 / 학습·검증", expanded=False):
    c1, c2, c3 = st.columns(3)
    with c1, common.card("fold-net"):
        common.chart(charts.fold_net_fig(folds), "fold-net")
    with c2, common.card("trigger"):
        meta = rep.get("meta", {})
        label = meta.get("judge_profile", "default") + ("+funding" if meta.get("funding") else "")
        sums = ud.list_summaries(common.path("AT_OUT_DIR"))
        sums = sums[sums["profile"] == label]
        if sums.empty:
            st.info(f"`summary/{label}/` 에 구간 run 요약이 없다.")
        else:
            runs = pd.concat([common.summary(p)[1] for p in sums["path"]], ignore_index=True)
            common.chart(charts.trigger_pie_fig(ud.trigger_pass_ratio(runs)), "trigger")
            st.caption(f"{label} · 구간 {', '.join(sums['span'])} · run {len(runs):,}")
    with c3, common.card("train-test"):
        common.chart(charts.train_test_fig(folds), "train-test")

b1, b2, b3 = st.columns([2, 1, 1])
with b1, common.card("recent"):
    st.subheader("최근 검증 거래")
    tr = common.wf_trades(name)
    if tr is None:
        common.no_equity_notice(name)
    else:
        recent = tr.sort_values("exit_ts", ascending=False).head(10)
        st.dataframe(common.trade_styler(recent), hide_index=True, width="stretch",
                     column_config=common.TRADE_CONFIG, key="recent-table")
with b2, common.card("todo"):
    st.subheader("사람 할 일")
    tasks = common.vault_tasks()
    manual = tasks[tasks["status"] == "manual"]
    if tasks.empty:
        st.caption("볼트 태스크를 찾지 못했다 (AT_VAULT_DIR).")
    elif manual.empty:
        st.caption("사람 조치가 필요한 태스크 없음")
    for _, t in manual.iterrows():
        st.checkbox(f"{t['id']} · {t['title']}", value=False, disabled=True, key=f"todo-{t['id']}")
with b3, common.card("phase"):
    st.subheader("Phase 진행")
    prog = ud.phase_progress(common.vault_tasks())
    for _, r in prog.iterrows():
        st.progress(float(r["ratio"]), text=f"Phase {int(r['phase'])} · {int(r['done'])}/{int(r['total'])}")
    cov = common.coverage()
    st.caption(f"1분봉 {cov['symbol']} {cov['n_days']:,}일 ({cov['first']} ~ {cov['last']}) · "
               f"표본 결측 {cov['sample_missing']}일")
