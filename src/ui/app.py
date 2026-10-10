"""AgentTrading 콘솔(127.0.0.1 전용). 실행: `scripts/ui.sh`.

- 에이전트: 전략(무엇으로 거래하나)·모니터·리스크 한도·킬스위치·계정·기록. 수동 주문 없음 — 설계 Obsidian
  `design/agent-strategy-page.md`, `design/trading-console.md`
- 백테스트 분석: 산출물 파일만 읽는 읽기 전용 화면 — 설계 Obsidian `design/user-dashboard.md`
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run src/ui/app.py` 는 스크립트 폴더만 sys.path 에 넣는다
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

PAGES = Path(__file__).resolve().parent / "views"  # `pages/` 는 Streamlit 이 자동 멀티페이지로 잡아 app.py 를 건너뛴다

st.set_page_config(page_title="AgentTrading", page_icon=":material/monitoring:", layout="wide")
nav = st.navigation({
    "에이전트": [
        st.Page(PAGES / "strategy.py", title="전략", icon=":material/psychology:", default=True),
        st.Page(PAGES / "console_monitor.py", title="모니터", icon=":material/monitor_heart:"),
        st.Page(PAGES / "console_risk.py", title="리스크", icon=":material/shield:"),
        st.Page(PAGES / "console_account.py", title="계정·연결", icon=":material/key:"),
        st.Page(PAGES / "console_history.py", title="거래 기록", icon=":material/receipt_long:"),
    ],
    "백테스트 분석": [
        st.Page(PAGES / "overview.py", title="개요", icon=":material/dashboard:"),
        st.Page(PAGES / "walkforward.py", title="워크포워드", icon=":material/timeline:"),
        st.Page(PAGES / "explore.py", title="전략 탐색", icon=":material/scatter_plot:"),
        st.Page(PAGES / "trades.py", title="거래 상세", icon=":material/candlestick_chart:"),
        st.Page(PAGES / "diagnose.py", title="진단", icon=":material/troubleshoot:"),
    ],
})
st.sidebar.caption("127.0.0.1 전용 · 수동 주문 없음 · 에이전트는 관문을 모두 통과한 전략만 쓴다")
nav.run()
