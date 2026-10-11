#!/usr/bin/env bash
# Kev-0.8B 로컬 System One 서버 (J0 실측·J2 섀도용). 저장소 밖에 설치하고 127.0.0.1 에만 바인딩한다.
#
#   scripts/systemone_server.sh setup    # clone/fetch → 커밋 고정 checkout → uv sync (잠금 파일 그대로)
#   scripts/systemone_server.sh start    # 포그라운드 실행(exec). 백그라운드는 호출 측에서 & 로
#   scripts/systemone_server.sh status   # GET /v1/models
#
# 커밋·HF 리비전은 scripts/systemone_probe.py 의 KEV_COMMIT·HF_REVISION 과 같아야 한다(테스트가 대조).
set -euo pipefail

KEV_REPO="https://github.com/jaredpalmer/kev.git"
KEV_DIR="${KEV_DIR:-$HOME/.local/share/agenttrading/kev}"
KEV_COMMIT="fc4a17e194bca35a48c57211392ec49ea54a94ac"
KEV_HF_MODEL="jaredpalmer/kev-0.8b"
KEV_HF_REVISION="bf75a6a8848ea6960ff2ed108d9ed44c2941174f"
HOST=127.0.0.1
PORT="${PORT:-8009}"
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"

check_head() {
  local head
  head="$(git -C "$KEV_DIR" rev-parse HEAD)"
  if [[ "$head" != "$KEV_COMMIT" ]]; then
    echo "Kev HEAD $head != 고정 커밋 $KEV_COMMIT" >&2
    exit 1
  fi
}

case "${1:-}" in
  setup)
    if [[ ! -d "$KEV_DIR/.git" ]]; then
      mkdir -p "$(dirname "$KEV_DIR")"
      git clone "$KEV_REPO" "$KEV_DIR"
    fi
    git -C "$KEV_DIR" fetch origin
    git -C "$KEV_DIR" checkout --detach "$KEV_COMMIT"
    check_head
    (cd "$KEV_DIR" && "$UV" sync --extra serve --frozen --python 3.13)
    ;;
  start)
    check_head
    cd "$KEV_DIR"
    exec "$UV" run --frozen --extra serve python -m kev.serve \
      --run "$KEV_HF_MODEL@$KEV_HF_REVISION" --host "$HOST" --port "$PORT"
    ;;
  status)
    curl -s "http://$HOST:$PORT/v1/models"
    ;;
  *)
    echo "usage: $0 {setup|start|status}" >&2
    exit 2
    ;;
esac
