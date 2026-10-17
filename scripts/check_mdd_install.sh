#!/usr/bin/env bash
# Fresh locked install in an isolated environment; source .venv is never changed.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'ERROR: uv is required' >&2; exit 1; }
install_tmp=$(mktemp -d "${TMPDIR:-/tmp}/agenttrading-mdd-install.XXXXXX")
trap 'rm -rf "$install_tmp"' EXIT
export UV_PROJECT_ENVIRONMENT="$install_tmp/venv"
uv sync --frozen --python 3.12 --group dev --extra auth
"$UV_PROJECT_ENVIRONMENT/bin/python" -m src.agent.drawdown_cli --help
"$UV_PROJECT_ENVIRONMENT/bin/python" -m src.ui.serve --help
"$UV_PROJECT_ENVIRONMENT/bin/python" -c 'import streamlit, authlib, httpx; from src.agent.service import TradingService; assert callable(TradingService.drawdown_status)'
"$UV_PROJECT_ENVIRONMENT/bin/python" - "$install_tmp" <<'PY'
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

assert sys.version_info[:2] == (3, 12)
temporary = Path(sys.argv[1])
with socket.socket() as address:
    address.bind(('127.0.0.1', 0))
    port = address.getsockname()[1]
environment = {key: value for key, value in os.environ.items() if not key.startswith('BYBIT_')}
environment.update(AT_AUTH_REQUIRED='0', AT_AUTH_EXTERNAL='0', AT_LIVE_ENABLED='0',
                   AT_AGENT_DIR=str(temporary / 'agent'), AT_OUT_DIR=str(temporary / 'out'),
                   AT_DATA_DIR=str(temporary / 'bars'), AT_VAULT_DIR='',
                   STREAMLIT_BROWSER_GATHER_USAGE_STATS='false')
for key in ('BYBIT_DEMO_API_KEY', 'BYBIT_DEMO_API_SECRET', 'BYBIT_LIVE_API_KEY', 'BYBIT_LIVE_API_SECRET'):
    environment[key] = ''
with (temporary / 'ui.log').open('w+') as log:
    process = subprocess.Popen([sys.executable, '-m', 'src.ui.serve', '--address', '127.0.0.1',
                                '--port', str(port)], env=environment, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('fresh-environment UI exited before becoming healthy')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=.5) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('fresh-environment UI health timeout')
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as response:
            assert response.status == 200 and b'<html' in response.read().lower()
    except Exception:
        log.flush()
        log.seek(0)
        print(log.read(), file=sys.stderr)
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
print('PASS: fresh Python 3.12 UI startup, HTTP root/health and cleanup')
PY
echo 'PASS: fresh frozen dev+auth install, MDD CLI, UI startup and imports'
