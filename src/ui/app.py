"""AgentTrading 콘솔. 실행: `scripts/ui.sh`(127.0.0.1) 또는 `python -m src.ui.serve`(배포 — DEPLOY.md, 외부 bind 는 인증 필수).

화면 구성(Obsidian `design/ui-ux-v2-benchmark.md` — 오픈소스 트레이딩 UI 벤치마크):
- 홈: 대시보드(첫 화면) — 에이전트 상태 한 줄 → 게이트 KPI → 자본곡선·낙폭 → 표
- 에이전트: 전략(무엇으로 거래하나)·모니터·거래 기록. 수동 주문 없음 — `design/agent-strategy-page.md`, `design/trading-console.md`
- 백테스트: 산출물 파일만 읽는 읽기 전용 화면 — `design/user-dashboard.md`
- 설정·안전: 리스크 한도·킬스위치, 계정·연결(모드 전환)
사이드바 아래에는 모드·정지·키·전략·판정 상태가 어느 화면에서나 보인다(네트워크 호출 없음).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run src/ui/app.py` 는 스크립트 폴더만 sys.path 에 넣는다
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from src.ui import auth, common, theme  # noqa: E402

PAGES = Path(__file__).resolve().parent / "views"  # `pages/` 는 Streamlit 이 자동 멀티페이지로 잡아 app.py 를 건너뛴다

st.set_page_config(page_title="AgentTrading", page_icon=":material/monitoring:", layout="wide")
auth.gate()  # 인증·bind 게이트 — 기본값(127.0.0.1·인증 없음)에서는 아무것도 하지 않는다. src/ui/auth.py
NAV = {
    "홈": [
        st.Page(PAGES / "overview.py", title="대시보드", icon=":material/dashboard:", default=True),
    ],
    "에이전트": [
        st.Page(PAGES / "agents.py", title="에이전트", icon=":material/smart_toy:"),
        st.Page(PAGES / "strategy.py", title="전략", icon=":material/psychology:"),
        st.Page(PAGES / "console_monitor.py", title="모니터", icon=":material/monitor_heart:"),
        st.Page(PAGES / "console_history.py", title="거래 기록", icon=":material/receipt_long:"),
    ],
    "백테스트": [
        st.Page(PAGES / "walkforward.py", title="워크포워드", icon=":material/timeline:"),
        st.Page(PAGES / "explore.py", title="전략 탐색", icon=":material/scatter_plot:"),
        st.Page(PAGES / "trades.py", title="거래 상세", icon=":material/candlestick_chart:"),
        st.Page(PAGES / "diagnose.py", title="진단", icon=":material/troubleshoot:"),
    ],
    "설정·안전": [  # 위험 증가 동작(킬스위치·LIVE 전환)을 한 그룹에. 모드 전환을 화면에 둘지는 platform-architecture 5절 D 미결 — 현행 유지
        st.Page(PAGES / "console_risk.py", title="리스크", icon=":material/shield:"),
        st.Page(PAGES / "console_account.py", title="계정·연결", icon=":material/key:"),
    ],
}
nav = st.navigation(NAV)
st.markdown(theme.CSS, unsafe_allow_html=True)
st.sidebar.markdown('<div class="terminal-brand"><span class="mark">▥</span><span>AgentTrading<small>RESEARCH / EXECUTION</small></span></div>', unsafe_allow_html=True)
common.sidebar_status()
st.sidebar.caption("기본 127.0.0.1 · 외부 공개는 로그인 + 허용 이메일(DEPLOY.md) · 수동 주문 없음 · 에이전트는 관문을 모두 통과한 전략만 쓴다")
nav.run()
