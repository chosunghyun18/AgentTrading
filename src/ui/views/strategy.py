"""전략 — 에이전트가 지금 어떤 전략으로 거래하는지, 후보 전략별 규칙·근거·관문 진행.

읽기 전용. 전략 목록은 `src/agent/strategies.py`(볼트 설계 문서 기준), 결과 수치는 워크포워드 산출물에서 읽는다.
"""

import streamlit as st

from src.agent import strategies as sg
from src.ui import common, theme, trading
from src.ui import data as ud
from src.ui import strategy_view as sv

common.setup("전략")
st.markdown(sv.CSS, unsafe_allow_html=True)
svc = trading.service()
trading.banner(svc)
reg = sg.registry()
now = sg.active(reg)

with common.card("agent-now"):
    st.subheader("에이전트가 지금 쓰는 전략")
    if now is None:
        st.markdown(f"<div class='agent-now'>없음 {theme.badge_html('에이전트 미가동', theme.INFO)}</div>",
                    unsafe_allow_html=True)
        st.markdown("에이전트는 아래 관문을 **모두** 통과한 전략만 쓴다: 워크포워드 게이트 → OOS 1회 → 페이퍼 8주 합격 → 사람 승인. "
                    "지금 이 조건을 만족하는 전략이 없어 자동 매매가 돌지 않고, 수동 주문 기능도 없다.")
        nxt = next((s for s in reg if s.status == "next"), None)
        if nxt:
            step, _ = sg.blocking_step(nxt)
            st.caption(f"다음 후보: {nxt.name} — 현재 단계 '{step}' ({nxt.notes.get(step, '대기')})")
        else:
            st.caption("다음 후보 없음 — 다음 가설 설계 대기(시도 이력표 기준)")
    else:
        st.markdown(f"<div class='agent-now'>{now.name} {sv.status_badge(now)}</div>", unsafe_allow_html=True)
        st.write(now.summary)

with common.card("pipeline"):
    st.subheader("관문 진행")
    st.markdown(sv.pipeline_html(reg), unsafe_allow_html=True)
    st.caption("칸에 마우스를 올리면 사유가 보인다. 관문 기준: Spec 3절(거래 ≥100 · Sharpe ≥1.0 · MDD ≤30% · DSR ≥0.95), "
               "3.1절(페이퍼 8주).")

for s in reg:
    with common.card(f"strategy-{s.id}"):
        st.markdown(f"### {s.name} {sv.status_badge(s)}", unsafe_allow_html=True)
        st.write(s.summary)
        left, right = st.columns([3, 2])
        with left:
            st.markdown("<div class='rule-h'>진입</div>", unsafe_allow_html=True)
            st.markdown(sv.bullets(s.entry))
            st.markdown("<div class='rule-h'>청산</div>", unsafe_allow_html=True)
            st.markdown(sv.bullets(s.exit))
            st.markdown("<div class='rule-h'>크기·위험</div>", unsafe_allow_html=True)
            st.markdown(sv.bullets(s.sizing))
        with right:
            st.markdown("<div class='rule-h'>근거 데이터</div>", unsafe_allow_html=True)
            st.write(s.evidence)
            st.markdown("<div class='rule-h'>파라미터</div>", unsafe_allow_html=True)
            st.markdown(sv.bullets(s.params) + (f"\n- 다중 비교 시행 수 N: {s.n_trials}" if s.n_trials else ""))
            st.caption(f"설계 문서: 볼트 `Projects/work/AgentTrading/{s.doc}`")

        rep = common.report(s.report) if s.report else None
        if rep is not None:
            st.markdown("<div class='rule-h'>워크포워드 결과</div>", unsafe_allow_html=True)
            common.verdict_line(rep)
            common.kpi_row(ud.gate_kpis(rep, "default"))
            sel = (rep.get("full_sample") or {}).get("selected")
            picks = sorted({f["selection"]["param_id"] for f in rep["folds"] if f.get("selection")})
            with st.expander(f"워크포워드가 고른 규칙을 문장으로 보기 (폴드 선택 {len(picks)}종)"):
                for pid in picks:
                    folds = [str(i) for i, f in enumerate(rep["folds"], 1)
                             if f.get("selection") and f["selection"]["param_id"] == pid]
                    st.markdown(f"**폴드 {', '.join(folds)}** · `{pid}`")
                    st.markdown(sv.bullets(sg.describe_v1(pid)))
                if sel:
                    st.markdown(f"**표본 전체에서 고른 규칙(OOS 후보였던 것)** · `{sel['param_id']}`")
                    st.markdown(sv.bullets(sg.describe_v1(sel["param_id"])))
