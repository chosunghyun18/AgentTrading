"""AI 백테스트 — 자연어 → 정형 명세(JSON) → 결정적 엔진 → 결과.

- AI(Claude)는 **번역만** 한다(`llm.py`). 계산은 `etf.py`(ETF 일봉)·`platform.py`(전략 플러그인, XBTUSD 1분봉)가 한다.
- 같은 명세는 늘 같은 결과. DB·파일 쓰기 없음(결과는 호출자가 쥔다).
설계: Obsidian `Projects/work/AgentTrading/task/ai-backtest.md`.
"""
