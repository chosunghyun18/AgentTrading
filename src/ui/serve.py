"""관리 화면 기동 런처 — bind 주소·인증 설정을 검사한 뒤 Streamlit 을 exec 한다(컨테이너 CMD).

    python -m src.ui.serve [--address A] [--port P]   # 기본 AT_BIND_ADDRESS(127.0.0.1)·AT_PORT(8501)

loopback 이 아닌 주소에 인증 요구(`AT_AUTH_REQUIRED=1`)도 외부 인증 선언(`AT_AUTH_REQUIRED=0 AT_AUTH_EXTERNAL=1`)도 없으면
종료코드 2 — fail-closed. 플래그 값이 이상해도(`enabled` 등) 종료코드 2. 허용 목록이 비어 있어도 기동은 하되 화면이
설정 안내만 보인다. 로컬 실행 스크립트 `scripts/ui.sh`(127.0.0.1 고정)는 그대로다.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from src.shared import settings

APP = Path(__file__).resolve().parent / "app.py"
EXIT_REFUSED = 2


def build_command(address: str, port: int, python: str = sys.executable) -> list[str]:
    return [python, "-m", "streamlit", "run", str(APP), "--server.address", address, "--server.port", str(port),
            "--server.headless", "true", "--browser.gatherUsageStats", "false"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.ui.serve", description="관리 화면 기동 런처(bind·인증 검사 후 streamlit exec)")
    p.add_argument("--address", default=None, help="bind 주소 (기본 AT_BIND_ADDRESS)")
    p.add_argument("--port", type=int, default=None, help="포트 (기본 AT_PORT)")
    return p


def main(argv: Sequence[str] | None = None, execv: Callable[[str, list[str]], object] = os.execv, err=None) -> int:
    err = err or sys.stderr
    a = build_parser().parse_args(argv)
    address = a.address or settings.get("AT_BIND_ADDRESS")
    port = a.port if a.port is not None else int(settings.get("AT_PORT"))
    try:
        ok, why = settings.bind_allowed(address, settings.auth_policy())
    except ValueError as e:
        print(f"기동 거부 — 설정 오류: {e}", file=err)
        return EXIT_REFUSED
    if not ok:
        print(f"기동 거부 — {why}", file=err)
        return EXIT_REFUSED
    print(f"관리 화면 기동 — {why}", file=err)
    cmd = build_command(address, port)
    execv(cmd[0], cmd)  # 성공하면 돌아오지 않는다
    return 0


if __name__ == "__main__":
    sys.exit(main())
