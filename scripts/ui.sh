#!/usr/bin/env bash
# AgentTrading 사용자 대시보드(읽기 전용) 실행 — 127.0.0.1 전용, 외부 공개·인증 없음.
#   scripts/ui.sh            # http://127.0.0.1:8501
#   PORT=8502 scripts/ui.sh  # 다른 포트
# autodev 진행 화면(8765)과는 별개다.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8501}"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/streamlit run src/ui/app.py \
  --server.address 127.0.0.1 --server.port "$PORT" --server.headless true \
  --browser.gatherUsageStats false
