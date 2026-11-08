"""관문 — 전략이 거쳐야 하는 관문 7개의 쉬운 설명·통과 조건·용어 풀이·전략별 현재 상태.

전략 페이지 관문 진행표의 머리글·칸에서 `gates?gate=<키>[&strategy=<id>]` 로 넘어온다. 읽기 전용.
설계: Obsidian `Projects/work/AgentTrading/task/ui-gate-detail-page.md`.
"""

import streamlit as st

from src.agent import strategies as sg
from src.ui import common
from src.ui import strategy_view as sv

common.setup("관문")
st.markdown(sv.CSS, unsafe_allow_html=True)
reg = sg.registry()
st.markdown(sv.INTRO)

if "gate-pick" not in st.session_state:  # 첫 진입만 URL 을 따른다. 이후 선택은 URL 에 다시 쓴다
    st.session_state["gate-pick"] = sv.gate_by_key(st.query_params.get("gate"))


def _picked() -> None:
    """관문을 바꾸면 전략 강조를 지우고, 선택 해제(같은 항목 다시 누름)는 직전 관문으로 되돌린다."""
    if st.session_state["gate-pick"] is None:
        st.session_state["gate-pick"] = st.session_state.get("gate-last", sg.STEPS[0])
    st.query_params.pop("strategy", None)


step = st.segmented_control("관문", sg.STEPS, key="gate-pick", on_change=_picked,
                            label_visibility="collapsed") or sg.STEPS[0]
st.session_state["gate-last"] = step
st.query_params["gate"] = sg.STEP_KEYS[step]
highlight = st.query_params.get("strategy")
gate = sv.GATES[step]

with common.card("gate-what"):
    n = sg.STEPS.index(step) + 1
    st.subheader(f"{n}. {step} — {gate.alias}")
    st.markdown("<div class='rule-h'>쉽게 말하면</div>", unsafe_allow_html=True)
    st.markdown(gate.plain)
    st.markdown("<div class='rule-h'>통과 조건</div>", unsafe_allow_html=True)
    st.markdown(sv.bullets(gate.criteria))
    with st.expander("정확한 기준 보기 (프로젝트 문서 원문)"):
        st.markdown(sv.bullets(gate.exact))
        st.markdown("기준 문서: " + " · ".join(f"볼트 `Projects/work/AgentTrading/{d}`" for d in gate.docs))

with common.card("gate-terms"):
    st.subheader("이 관문에 나오는 용어")
    st.markdown(sv.terms_html(gate.terms), unsafe_allow_html=True)

with common.card("gate-status"):
    st.subheader("전략별 상태")
    st.markdown(sv.gate_status_html(step, reg, highlight), unsafe_allow_html=True)
    st.markdown('<a href="strategy" target="_self">← 전략 페이지 관문 진행표로</a>', unsafe_allow_html=True)
