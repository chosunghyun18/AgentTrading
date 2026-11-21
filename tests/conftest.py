"""전체 테스트 공통 — 로컬 `.env` 설정·API 키가 테스트로 새지 않게 막는다.

`shared.settings.get` 은 환경변수 → `.env` 순이라, 환경변수로 넣으면 `.env` 의 `AT_MARKET_SOURCE=db` 같은 값을 덮는다.
AI 키는 값을 덮을 수 없어(빈 환경변수는 `.env` 로 넘어간다) `.env` 조회 자체를 끊는다 — 키가 생겨도 테스트가 실제 호출을
하지 않게 하는 방어선이다. 키 해석 자체를 보는 테스트는 `llm.dotenv_values` 를 자기 값으로 다시 패치한다.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_local_env(monkeypatch):
    monkeypatch.setenv("AT_ENV", "local")
    monkeypatch.setenv("AT_MARKET_SOURCE", "parquet")
    monkeypatch.setenv("AT_MARKET_DB_URL", "")
    from src.aibt import llm
    for key in (llm.ANTHROPIC_KEY, llm.OPENROUTER_KEY):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(llm, "dotenv_values", lambda *a, **k: {})
