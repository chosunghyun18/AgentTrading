# AgentTrading 관리 화면(ui) 이미지 — Python 3.12 + uv 잠금(uv.lock). 절차: DEPLOY.md · 설계: Obsidian design/platform-architecture.md 3.1절
# 데이터·거래소 키·secrets 는 이미지에 넣지 않는다(.dockerignore). 에이전트 런타임 서비스는 W2 에서 같은 이미지에 CMD 만 바꿔 붙인다.

# ---- 1단계: 의존성 설치(uv, 잠금 그대로; 프로젝트는 패키지가 아니라 코드만 복사) ----
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --extra auth

# ---- 2단계: 실행 이미지(non-root, uv 없음) ----
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" PYTHONPATH=/app \
    AT_BIND_ADDRESS=0.0.0.0 AT_PORT=8501 \
    AT_OUT_DIR=/app/data/out/backtest AT_DATA_DIR=/app/data/raw/normalized/bitmex \
    AT_AGENT_DIR=/app/data/agent AT_RISK_FILE=/app/data/config/risk.yaml
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app src ./src
COPY --chown=app:app .streamlit/config.toml ./.streamlit/config.toml
RUN mkdir -p /app/data && chown -R app:app /app/data
USER app
EXPOSE 8501
# 컨테이너 안은 0.0.0.0 bind 라 src.ui.serve 가 인증 설정(AT_AUTH_REQUIRED=1 또는 AT_AUTH_REQUIRED=0 AT_AUTH_EXTERNAL=1)을 요구한다.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,sys,urllib.request; p=os.environ.get('AT_PORT','8501'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/_stcore/health', timeout=3).status == 200 else 1)"
CMD ["python", "-m", "src.ui.serve"]
