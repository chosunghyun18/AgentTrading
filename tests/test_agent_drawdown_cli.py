"""Run the actual CLI in a subprocess with explicit runpy factory injection."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BOOT = '''
import runpy, sys
from pathlib import Path
from src.agent import service
from tests.agent_fakes import FakeClient
root = Path(sys.argv.pop(1))
c = FakeClient()
c.equity = float(sys.argv.pop(1))
service.FACTORY = lambda mode: service.TradingService(c, mode, root, root/'risk.yaml')
runpy.run_module('src.agent.drawdown_cli', run_name='__main__')
'''

def run(tmp_path, *args, equity=100):
    return subprocess.run([sys.executable, '-c', BOOT, str(tmp_path), str(equity), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=15)

def test_cli_persistence_breach_rebase_resume(tmp_path):
    result = run(tmp_path, 'set-limit', '--percent', '80')
    assert result.returncode == 0, result.stderr
    assert json.loads(run(tmp_path, 'status').stdout)['limit_fraction'] == .8
    assert run(tmp_path, 'initialize').returncode != 0
    assert run(tmp_path, 'initialize', '--confirm').returncode == 0
    assert json.loads(run(tmp_path, 'poll', equity=20).stdout)['latched']
    assert run(tmp_path, 'resume', '--confirm', equity=20).returncode != 0
    assert run(tmp_path, 'reset-baseline', '--confirm', '--reason', 'cash flow', equity=20).returncode == 0
    assert json.loads(run(tmp_path, 'status').stdout)['latched']
    assert run(tmp_path, 'resume', '--confirm', equity=20).returncode == 0
    assert not json.loads(run(tmp_path, 'status').stdout)['latched']

def test_watch_bounded_invalid_and_sigint(tmp_path):
    assert run(tmp_path, 'initialize', '--confirm').returncode == 0
    result = run(tmp_path, 'watch', '--interval', '.01', '--iterations', '2')
    assert result.returncode == 0 and len(result.stdout.splitlines()) == 2
    assert run(tmp_path, 'watch', '--interval', 'nan').returncode != 0
    assert run(tmp_path, 'set-limit', '--percent', '100').returncode != 0
    proc = subprocess.Popen([sys.executable, '-c', BOOT, str(tmp_path), '100', 'watch', '--interval', '.05'],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert json.loads(proc.stdout.readline())['enrolled']
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=5)
        assert proc.returncode == 0 and 'Traceback' not in err
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_cli_reports_invalid_store_and_watch_observation_failure(tmp_path):
    # Unenrolled is an ordinary status; attempting observations is an error.
    assert run(tmp_path, 'status').returncode == 0
    result = run(tmp_path, 'watch', '--iterations', '1', '--interval', '.01')
    assert result.returncode == 1 and json.loads(result.stdout)['blocked']
    (tmp_path/'drawdown.json').write_text('invalid json')
    result = run(tmp_path, 'status')
    assert result.returncode == 1 and json.loads(result.stdout)['blocked']
