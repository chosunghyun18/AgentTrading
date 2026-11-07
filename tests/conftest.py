"""전체 테스트 공통 — 로컬 `.env` 의 시세 소스 설정이 테스트로 새지 않게 parquet·local 로 고정한다.

`shared.settings.get` 은 환경변수 → `.env` 순이라, 환경변수로 넣으면 `.env` 의 `AT_MARKET_SOURCE=db` 같은 값을 덮는다.
"""

import pytest


@pytest.fixture(autouse=True)
def _market_source_parquet(monkeypatch):
    monkeypatch.setenv("AT_ENV", "local")
    monkeypatch.setenv("AT_MARKET_SOURCE", "parquet")
    monkeypatch.setenv("AT_MARKET_DB_URL", "")
