#!/usr/bin/env python3
"""Real Chrome + Node22 CDP smoke against a temporary, fake-only Streamlit app.

Run with the project Python. Requires Chrome and Node >=22; override executables
with CHROME and MDD_NODE. All app state/profile/files are temporary; no API keys.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
APP = r'''
import json, os, runpy, socket
from pathlib import Path
import streamlit as st
from src.agent import service, settings
from src.agent.service import TradingService
from tests.agent_fakes import FakeClient
# Hard boundary: even an accidental real client request cannot leave loopback.
_original_connect = socket.socket.connect

def _local_connect(sock, address, _connect=_original_connect):
    if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
        raise RuntimeError("browser fixture forbids external network")
    return _connect(sock, address)
if not getattr(socket.socket.connect, "_mdd_local", False):
    _local_connect._mdd_local = True
    socket.socket.connect = _local_connect
settings.load_env = lambda: None
if "fake" not in st.session_state:
    st.session_state.fake = FakeClient()
    st.session_state.equity = 100.0
client = st.session_state.fake
original_wallet = FakeClient.wallet

def wallet():
    result = original_wallet(client)
    result["totalEquity"] = str(st.session_state.equity)
    return result
client.wallet = wallet
svc = TradingService(client, "demo", Path(os.environ["AT_AGENT_DIR"]), Path(os.environ["MDD_RISK_PATH"]))
service.FACTORY = lambda mode: svc

def loss():
    st.session_state.equity = 20.0
    client.orders = [
        {"orderId": "entry", "symbol": "BTCUSDT", "orderLinkId": "atc-browser", "qty": "0.01", "reduceOnly": False},
        {"orderId": "protect", "symbol": "BTCUSDT", "orderLinkId": "atc-protect", "qty": "0.01", "reduceOnly": True},
        {"orderId": "close", "symbol": "BTCUSDT", "orderLinkId": "atg-close", "qty": "0.01", "closeOnTrigger": True},
    ]
st.sidebar.button("TEST equity loss to 20", on_click=loss)
runpy.run_path(str(Path(os.environ["MDD_ROOT"]) / "src/ui/views/console_risk.py"), run_name="__main__")
dd = svc.drawdown_status()
Path(os.environ["MDD_EVIDENCE"]).write_text(json.dumps({"calls": client.calls, "orders": client.orders}))
st.code("TEST_STATE " + json.dumps({"limit": dd.get("limit_fraction"), "enrolled": dd.get("enrolled"),
    "latched": dd.get("latched"), "blocked": dd.get("blocked"), "drawdown": dd.get("drawdown"),
    "history": len(dd.get("history") or []), "orders": [o["orderId"] for o in client.orders]}, sort_keys=True))
'''

CDP = r'''
const [port, url] = process.argv.slice(1);
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let id = 0; const pending = new Map();
ws.onmessage = async event => {
  const msg = JSON.parse(event.data);
  if (msg.method === 'Fetch.requestPaused') {
    const u = new URL(msg.params.request.url);
    const local = ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname);
    await call(local ? 'Fetch.continueRequest' : 'Fetch.failRequest', local ?
      {requestId: msg.params.requestId} : {requestId: msg.params.requestId, errorReason: 'BlockedByClient'});
  }
  if (pending.has(msg.id)) {
    const {resolve,reject} = pending.get(msg.id); pending.delete(msg.id);
    msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
  }
};
const call = (method, params = {}) => new Promise((resolve,reject) => {
  pending.set(++id,{resolve,reject}); ws.send(JSON.stringify({id,method,params}));
});
const evaluate = async expression => {
  const r = await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
};
const sleep = ms => new Promise(r => setTimeout(r,ms));
async function until(expression) {
  for(let i=0;i<200;i++) {if(await evaluate(expression)) return; await sleep(100);}
  throw new Error(`Timeout: ${expression}\nDOM: ${await evaluate('document.body.innerText')}`);
}
const button = label => `[...document.querySelectorAll('button')].find(b=>b.innerText.trim()===${JSON.stringify(label)})`;
async function click(label) {
  await until(`!!(${button(label)}) && !(${button(label)}).disabled`);
  await evaluate(`(${button(label)}).click()`); await sleep(400);
}
async function check(label) {
  const expr = `[...document.querySelectorAll('label')].find(l=>l.innerText.includes(${JSON.stringify(label)}))`;
  await until(`!!(${expr})?.querySelector('input')`);
  await evaluate(`if(!(${expr}).querySelector('input').checked) (${expr}).querySelector('input').click()`);
  await until(`!!(${expr})?.querySelector('input')?.checked`); await sleep(500);
}
async function input(label,value) {
  const expr = `[...document.querySelectorAll('input')].find(i=>i.getAttribute('aria-label')===${JSON.stringify(label)})`;
  await until(`!!(${expr})`); await evaluate(`(${expr}).focus(); (${expr}).select()`);
  await call('Input.insertText',{text:value});
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
  await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
  await sleep(500);
}
async function state(expected) {
  await until(`(()=>{const text=[...document.querySelectorAll('code')].map(e=>e.innerText).find(t=>t.startsWith('TEST_STATE '));
    if(!text)return false; const s=JSON.parse(text.slice(11));
    return Object.entries(${JSON.stringify(expected)}).every(([k,v])=>JSON.stringify(s[k])===JSON.stringify(v));})()`);
}
await call('Page.enable');
await call('Fetch.enable',{patterns:[{urlPattern:'http*',requestStage:'Request'}]});
await call('Page.navigate',{url});
await until(`document.body.innerText.includes('계정 낙폭 보호 (MDD)')`);
await input('계정 낙폭 한도 (%)','80'); await click('낙폭 한도 저장'); await state({limit:0.8,enrolled:false});
await check('계정 전체가 flat이고'); await click('계정 낙폭 초기화'); await state({enrolled:true,latched:false});
await click('TEST equity loss to 20'); await click('계정 낙폭 관측');
await state({latched:true,blocked:true,drawdown:0.8,orders:['protect','close']});
await check('최신 낙폭이 한도 미만임을'); await click('낙폭 보호 재개');
await until(`document.body.innerText.includes('낙폭 보호 재개 실패')`); await state({latched:true});
await input('기준금 재설정 사유 (입출금 조정 등)','browser cashflow reconciliation');
await check('운용을 멈췄고 계정 전체 flat'); await click('낙폭 기준금 재설정');
await state({latched:true,history:1,drawdown:0});
await check('최신 낙폭이 한도 미만임을'); await click('낙폭 보호 재개'); await state({latched:false,blocked:false});
console.log('PASS: real Chrome DOM save80 -> enroll100 -> loss20 exact80% -> scoped cancel/latch -> blocked resume -> rebase remains latched -> separate resume');
ws.close();
'''


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    node = os.environ.get("MDD_NODE") or shutil.which("node")
    if not node or int(subprocess.check_output([node, "--version"], text=True).lstrip("v").split(".")[0]) < 22:
        raise SystemExit("ERROR: Node 22+ required (native WebSocket); set MDD_NODE to its executable. Test was NOT skipped.")
    chrome = os.environ.get("CHROME") or shutil.which("google-chrome") or shutil.which("chromium") or \
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not Path(chrome).is_file():
        raise SystemExit("ERROR: Chrome/Chromium required; set CHROME. Test was NOT skipped.")
    server = browser = None
    with tempfile.TemporaryDirectory(prefix="agenttrading-mdd-browser-") as directory:
        tmp = Path(directory)
        app = tmp / "app.py"
        app.write_text(APP)
        env = {k: v for k, v in os.environ.items() if not k.startswith("BYBIT_")}
        env.update(PYTHONPATH=str(ROOT), MDD_ROOT=str(ROOT), AT_AGENT_DIR=str(tmp / "agent"),
                   MDD_EVIDENCE=str(tmp / "fixture-evidence.json"),
                   MDD_RISK_PATH=str(tmp / "risk.yaml"), AT_AUTH_REQUIRED="0",
                   STREAMLIT_BROWSER_GATHER_USAGE_STATS="false")
        port = free_port()
        with (tmp / "streamlit.log").open("w+") as log:
            try:
                server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(app),
                    "--server.address=127.0.0.1", f"--server.port={port}", "--server.headless=true",
                    "--browser.gatherUsageStats=false", "--server.fileWatcherType=none"], cwd=ROOT, env=env,
                    stdout=log, stderr=subprocess.STDOUT)
                for _ in range(150):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=.2) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError("Streamlit exited")
                        time.sleep(.1)
                else:
                    raise RuntimeError("Streamlit health timeout")
                profile = tmp / "profile"
                browser = subprocess.Popen([chrome, "--headless=new", "--no-first-run", "--no-default-browser-check",
                    "--disable-background-networking", "--disable-component-update", "--disable-sync",
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1, EXCLUDE [::1]", "--remote-debugging-port=0",
                    f"--user-data-dir={profile}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                port_file = profile / "DevToolsActivePort"
                for _ in range(150):
                    if port_file.exists():
                        break
                    if browser.poll() is not None:
                        raise RuntimeError("Chrome exited")
                    time.sleep(.1)
                else:
                    raise RuntimeError("Chrome CDP startup timeout")
                result = subprocess.run([node, "--input-type=module", "-e", CDP,
                    port_file.read_text().splitlines()[0], f"http://127.0.0.1:{port}"],
                    text=True, capture_output=True, timeout=100)
                if result.returncode:
                    raise RuntimeError(result.stdout + result.stderr)
                evidence = json.loads((tmp / "fixture-evidence.json").read_text())
                assert not [call for call in evidence["calls"] if call[0] in ("create_order", "set_leverage", "cancel_all")]
                assert {order["orderId"] for order in evidence["orders"]} == {"protect", "close"}
                store = json.loads((tmp / "agent" / "drawdown.json").read_text())
                assert store["limit_fraction"] == .8
                state = next(iter(store["accounts"].values()))
                assert not state["latched"] and len(state["history"]) == 1
                assert state["history"][0]["max_drawdown"] == .8
                events = [json.loads(line)["event"] for line in (tmp / "agent" / "audit.jsonl").read_text().splitlines()]
                assert {"drawdown_limit_saved", "drawdown_initialized", "drawdown_breached", "drawdown_rebased", "drawdown_resumed"} <= set(events)
                print(result.stdout.strip())
                print('PASS: persisted policy, breach history, audit events, protective orders and zero entry transmissions')
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                stop(browser)
                stop(server)


if __name__ == "__main__":
    main()
