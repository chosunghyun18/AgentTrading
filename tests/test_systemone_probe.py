"""scripts/systemone_probe.py — 네트워크·GPU 없이 모의 전송으로 집계·판별력·fail-closed·리포트를 검증한다."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("systemone_probe", ROOT / "scripts" / "systemone_probe.py")
sp = importlib.util.module_from_spec(_spec)
sys.modules["systemone_probe"] = sp
_spec.loader.exec_module(sp)

SERVER_SH = ROOT / "scripts" / "systemone_server.sh"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def ok_payload(q1, tokens=683):
    return {"model": "kev-latest", "answers": {"take": {"noul": q1}},
            "usage": {"input_tokens": tokens, "output_tokens": 22}, "latency_ms": 420.0}


def fn_transport(clock, q1_of_state, latency_ms=430.0):
    """state → Q1 함수형 모의 전송. 호출마다 시계를 latency_ms 만큼 진행한다."""
    def t(body, timeout):
        clock.t += latency_ms / 1000
        return 200, ok_payload(q1_of_state(body["state"]))
    return t


def informative_q1(state):
    """판별력 ①②③ 를 모두 충족하는 모의 응답: trigger_z 크기·추세 효율에 단조, 펀딩 라벨 무시."""
    z = abs(state["proposed_trade"]["trigger_z"])
    te = state["trend_efficiency_240m"]
    return min(1.0, max(0.0, 0.2 + 0.15 * (z - 2.0) + 0.4 * (0.9 - te)))


# --- 1. state 재현 ------------------------------------------------------------------------------------------------


def test_states_reproduce_fixed_hash_and_have_no_absolute_keys():
    states = sp.make_states(20)
    assert sp.states_sha256(states) == sp.STATES20_SHA256
    assert sp.make_states(sp.N_LATENCY)[:20] == states
    banned = re.compile(r"date|time|ts|price|symbol|ticker", re.I)

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    for s in states:
        assert not [k for k in keys(s) if banned.search(k)]
    body = sp.request_body(states[0])
    assert set(body["questions"]) == {"take"}
    assert body["questions"]["take"] == {"type": "noul", "instructions": sp.Q1}
    assert body["model"] == "kev-latest"


# --- 2. 지연 집계 -------------------------------------------------------------------------------------------------


def test_latency_stats_match_numpy_and_deadline_boundary():
    walls = list(np.linspace(100, 1000, 91))  # 10 ms 간격, 550 정각 포함
    results = [sp.CallResult(True, None, 0.5, w) for w in walls]
    lat = sp.latency_stats(results)
    for q, key in ((50, "p50_ms"), (95, "p95_ms"), (99, "p99_ms")):
        assert lat[key] == pytest.approx(np.percentile(walls, q))
    assert 550.0 in walls
    assert lat["over_deadline"] == sum(w > 550 for w in walls) == 45
    assert lat["over_deadline_ratio"] == pytest.approx(45 / 91)


def test_latency_counts_failures_as_over_and_excludes_them_from_percentiles():
    results = [sp.CallResult(True, None, 0.5, 400.0)] * 8 + [sp.CallResult(False, "timeout", None, 5000.0)] * 2
    lat = sp.latency_stats(results)
    assert lat["n"] == 10 and lat["n_ok"] == 8
    assert lat["p99_ms"] == 400.0
    assert lat["over_deadline"] == 2 and lat["over_deadline_ok_only"] == 0
    assert lat["over_deadline_ratio"] == pytest.approx(0.2)


def test_call_once_measures_wall_with_injected_clock():
    clock = Clock()
    r = sp.call_once(fn_transport(clock, lambda s: 0.5, latency_ms=551.0), sp.make_states(1)[0], clock=clock)
    assert r.ok and r.wall_ms == pytest.approx(551.0)
    assert r.input_tokens == 683 and r.output_tokens == 22 and r.model == "kev-latest"


# --- 3. fail-closed ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("behaviour, reason", [
    (requests.ConnectionError("refused"), "connection"),
    (requests.Timeout("slow"), "timeout"),
    (requests.ReadTimeout("slow"), "timeout"),
    ((422, {"detail": "x"}), "http_422"),
    ((500, None), "http_500"),
    ((200, {"model": "kev-latest", "usage": {}}), "schema"),
    ((200, ok_payload(1.3)), "schema"),
    ((200, ok_payload("x")), "schema"),
    ((200, ok_payload(float("nan"))), "schema"),
    ((200, ok_payload(True)), "schema"),
    ((200, None), "schema"),
])
def test_call_once_fail_closed(behaviour, reason):
    def t(body, timeout):
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour
    r = sp.call_once(t, sp.make_states(1)[0], clock=Clock())
    assert r.ok is False and r.q1 is None and r.reason == reason


def test_harness_runs_to_end_and_counts_errors_by_reason():
    clock = Clock()
    n = {"i": 0}
    plan = {7: requests.ConnectionError(), 8: requests.Timeout(), 9: (500, None), 10: (200, ok_payload(2.0)),
            11: (200, None)}

    def t(body, timeout):
        n["i"] += 1
        clock.t += 0.4
        b = plan.get(n["i"])
        if isinstance(b, Exception):
            raise b
        return b if b else (200, ok_payload(informative_q1(body["state"])))

    res = sp.run_probe(t, n_latency=100, clock=clock)
    assert len(res["rows"]) == 5 + 100 + 100 + 10 + 40
    # 호출 7–11 = 지연 단계 i=1–5 (예열 5회 뒤)
    assert res["errors"] == {"connection": 1, "http_500": 1, "schema": 2, "timeout": 1}
    assert res["n_errors"] == 5
    assert res["latency"]["n_ok"] == 95 and res["latency"]["over_deadline"] == 5
    assert res["judgement"]["verdict"] == "human_report"
    assert any("오류 5건" in r for r in res["judgement"]["reasons"])


def test_warm_errors_are_not_counted():
    clock = Clock()
    n = {"i": 0}

    def t(body, timeout):
        n["i"] += 1
        clock.t += 0.4
        if n["i"] <= 2:
            raise requests.ConnectionError()
        return 200, ok_payload(informative_q1(body["state"]))

    res = sp.run_probe(t, n_latency=20, clock=clock)
    assert res["n_errors"] == 0 and res["errors"] == {}


# --- 4. 결정성 ----------------------------------------------------------------------------------------------------


def test_determinism_detects_tiny_change():
    same = {i: [sp.CallResult(True, None, 0.5123, 400.0) for _ in range(10)] for i in range(10)}
    d = sp.determinism(same)
    assert d["matched"] == 10 and d["pass"]
    for k in range(10):
        groups = {i: [sp.CallResult(True, None, 0.5123, 400.0) for _ in range(10)] for i in range(10)}
        groups[3][k] = sp.CallResult(True, None, 0.5123 + 1e-6, 400.0)
        d = sp.determinism(groups)
        assert d["matched"] == 9 and not d["pass"] and not d["per_state"][3]["match"]


def test_determinism_failed_call_is_mismatch():
    groups = {0: [sp.CallResult(True, None, 0.5, 400.0)] * 9 + [sp.CallResult(False, "timeout", None, 5000.0)]}
    assert sp.determinism(groups)["matched"] == 0


def test_determinism_in_run_with_drifting_repeat():
    clock = Clock()
    seen = {}

    def t(body, timeout):
        clock.t += 0.4
        key = json.dumps(body["state"], sort_keys=True)
        seen[key] = seen.get(key, 0) + 1
        q = informative_q1(body["state"]) + (1e-6 if seen[key] == 5 else 0.0)
        return 200, ok_payload(q)

    res = sp.run_probe(t, n_latency=20, clock=clock)
    # state i 는 지연 단계 1회 + 결정성 10회 → 결정성 4번째 호출이 흔들림
    assert res["determinism"]["matched"] == 0
    assert "결정성 0/10" in res["judgement"]["reasons"]


# --- 5. 판별력 ----------------------------------------------------------------------------------------------------


def _run_q(fn, n_latency=20):
    clock = Clock()
    return sp.run_probe(fn_transport(clock, fn), n_latency=n_latency, clock=clock)


def test_discriminability_passes_for_informative_responder():
    res = _run_q(informative_q1)
    d = res["discriminability"]
    assert d["spread"]["pass"] and d["spread"]["pstdev"] >= 0.05
    assert d["monotonic"]["trigger_z"]["pass"] and d["monotonic"]["trigger_z"]["direction"] == "up"
    assert d["monotonic"]["trend_efficiency_240m"]["pass"] and d["monotonic"]["trend_efficiency_240m"]["direction"] == "down"
    assert d["invariance"]["pass"] and d["invariance"]["n_pairs"] == 40
    assert d["pass"]


def test_discriminability_constant_fails_spread_and_flat_monotonic():
    d = _run_q(lambda s: 0.51)["discriminability"]
    assert not d["spread"]["pass"]
    assert d["monotonic"]["trigger_z"]["direction"] == "flat" and not d["monotonic"]["trigger_z"]["pass"]
    assert not d["monotonic"]["pass"]
    assert d["invariance"]["pass"]
    assert not d["pass"]


def test_discriminability_non_monotonic_fails():
    def wiggle(s):
        z = abs(s["proposed_trade"]["trigger_z"])
        return informative_q1(s) + (0.2 if abs(z - 2.75) < 1e-9 else 0.0)
    d = _run_q(wiggle)["discriminability"]
    assert d["monotonic"]["trigger_z"]["direction"] == "mixed"
    assert not d["monotonic"]["pass"] and d["monotonic"]["trend_efficiency_240m"]["pass"]


def test_discriminability_small_span_fails():
    seq = [0.5, 0.502, 0.504, 0.506, 0.509]
    d = sp.discriminability([0.1, 0.9] * 10, seq, [0.9, 0.5, 0.4, 0.3, 0.1], [(0.5, 0.5)] * 40)
    assert not d["monotonic"]["trigger_z"]["pass"] and d["monotonic"]["trend_efficiency_240m"]["pass"]


def test_discriminability_funding_sensitive_fails_invariance():
    bump = {"negative": 0.0, "neutral": 0.02, "positive": 0.04}
    d = _run_q(lambda s: informative_q1(s) * 0.9 + bump[s["funding_rate_label"]])["discriminability"]
    assert d["spread"]["pass"] and d["monotonic"]["pass"]
    assert not d["invariance"]["pass"] and d["invariance"]["n_violations"] > 0


def test_discriminability_missing_values_fail():
    d = sp.discriminability([0.1, None] * 10, [0.1, 0.2, None, 0.4, 0.5], [0.5] * 5, [(0.5, None)] * 40)
    assert not d["spread"]["pass"] and not d["monotonic"]["pass"] and not d["invariance"]["pass"]


def test_trigger_z_variant_keeps_sign():
    s = next(x for x in sp.make_states(20) if x["proposed_trade"]["trigger_z"] < 0)
    assert sp.trigger_z_variant(s, 3.5)["proposed_trade"]["trigger_z"] == -3.5
    assert s["proposed_trade"]["trigger_z"] != -3.5  # 원본 불변
    labels = [lab for lab, _ in sp.funding_variants(s)]
    assert len(labels) == 2 and s["funding_rate_label"] not in labels


# --- 6. 판정 ------------------------------------------------------------------------------------------------------


def _good():
    lat = {"over_deadline_ratio": 0.0}
    det = {"matched": 10, "n_states": 10, "pass": True}
    disc = {k: {"pass": True} for k in ("spread", "monotonic", "invariance")}
    return lat, det, disc


def test_judge_pass_and_each_failure():
    lat, det, disc = _good()
    assert sp.judge(lat, 0, det, disc)["verdict"] == "pass"
    assert sp.judge({"over_deadline_ratio": 0.05}, 0, det, disc)["verdict"] == "pass"
    assert sp.judge({"over_deadline_ratio": 0.06}, 0, det, disc)["verdict"] == "human_report"
    assert sp.judge(lat, 1, det, disc)["verdict"] == "human_report"
    assert sp.judge(lat, 0, {"matched": 9, "n_states": 10, "pass": False}, disc)["verdict"] == "human_report"
    bad = dict(disc, spread={"pass": False})
    j = sp.judge(lat, 0, det, bad)
    assert j["verdict"] == "human_report" and j["reasons"] == ["판별력 ① 분산 미달"]


# --- 7. 리포트·check-report ---------------------------------------------------------------------------------------


def _snapshot():
    return {"loadavg": [1.0, 1.0, 1.0], "top_cpu": ["1 2.0 python"]}


def _report(tmp_path, fn=informative_q1, day="20261011", transport_name="http"):
    clock = Clock()
    return sp.run_and_report(fn_transport(clock, fn), tmp_path, transport_name=transport_name,
                             base_url="http://127.0.0.1:8009", clock=clock, snapshot=_snapshot, day=day)


def test_full_run_report_contents(tmp_path):
    jl, md, summary = _report(tmp_path)
    assert jl.name == "probe_20261011.jsonl" and md.name == "probe_20261011.md"
    lines = jl.read_text().strip().splitlines()
    assert len(lines) == 1155 + 1
    last = json.loads(lines[-1])["summary"]
    assert set(sp.SUMMARY_KEYS) <= set(last)
    assert last["n_latency"] == 1000 and last["verdict"] == "pass" and last["model"] == "kev-latest"
    assert last["p50_ms"] == pytest.approx(430.0)
    text = md.read_text()
    for needle in ("p50", "p95", "p99", "마감 초과", "오류", "결정성", "입력 토큰", "① 분산", "② 단조성 (a)",
                   "② 단조성 (b)", "③ 불변성", "판정: pass", sp.KEV_COMMIT, sp.HF_REVISION, "load"):
        assert needle in text, needle
    assert sp.check_report(tmp_path) == []


def test_human_report_verdict_in_markdown(tmp_path):
    jl, md, summary = _report(tmp_path, fn=lambda s: 0.51)
    assert summary["verdict"] == "human_report"
    assert "판정: 사람 보고" in md.read_text()
    assert sp.check_report(tmp_path) == []  # 판정과 무관하게 완전하면 통과


def test_same_day_rerun_gets_suffix_and_latest_selection(tmp_path):
    _report(tmp_path)
    jl2, md2, _ = _report(tmp_path)
    assert jl2.name == "probe_20261011-2.jsonl" and md2.exists()
    assert (tmp_path / "probe_20261011.jsonl").exists()
    assert sp.latest_report(tmp_path).name == "probe_20261011-2.jsonl"
    for k in range(3, 11):
        (tmp_path / f"probe_20261011-{k}.jsonl").write_text("x")
    assert sp.next_report_stem(tmp_path, "20261011") == "probe_20261011-11"
    assert sp.latest_report(tmp_path).name == "probe_20261011-10.jsonl"
    (tmp_path / "probe_20261012.jsonl").write_text("x")
    assert sp.latest_report(tmp_path).name == "probe_20261012.jsonl"


def test_check_report_failures(tmp_path):
    assert sp.check_report(tmp_path / "none")
    assert sp.check_report(tmp_path)
    _report(tmp_path, transport_name="mock")
    assert any("transport" in p for p in sp.check_report(tmp_path))

    jl = sp.latest_report(tmp_path)
    lines = jl.read_text().strip().splitlines()
    summary = json.loads(lines[-1])["summary"]

    def rewrite(**kw):
        jl.write_text("\n".join(lines[:-1] + [json.dumps({"summary": dict(summary, transport="http", **kw)})]) + "\n")

    rewrite()
    assert sp.check_report(tmp_path) == []
    rewrite(n_latency=999)
    assert any("n_latency" in p for p in sp.check_report(tmp_path))
    rewrite(base_url="http://10.0.0.5:8009")
    assert any("loopback" in p for p in sp.check_report(tmp_path))
    rewrite(kev_commit="0" * 40)
    assert any("kev_commit" in p for p in sp.check_report(tmp_path))
    rewrite()
    jl.with_suffix(".md").unlink()
    assert any(".md" in p for p in sp.check_report(tmp_path))


def test_check_report_cli_exit_codes(tmp_path):
    cli = [sys.executable, str(ROOT / "scripts" / "systemone_probe.py"), "check-report", "--out-dir"]
    assert subprocess.run(cli + [str(tmp_path / "none")], capture_output=True).returncode != 0
    _report(tmp_path)
    assert subprocess.run(cli + [str(tmp_path)], capture_output=True).returncode == 0


# --- 8. HttpTransport ---------------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.calls, self.responses = [], list(responses)

    def post(self, url, **kw):
        self.calls.append((self, url, kw))
        return self.responses.pop(0)

    def get(self, url, **kw):
        return FakeResponse(200, {"object": "list", "data": [{"id": "kev-latest",
                                                               "prefix_cache": {"hits": 3, "misses": 7}}]})


def test_http_transport_reuses_session_and_sends_expected_request():
    sess = FakeSession([FakeResponse(200, ok_payload(0.4)), FakeResponse(200, None)])
    t = sp.HttpTransport("http://127.0.0.1:8009/", session=sess)
    body = sp.request_body(sp.make_states(1)[0])
    assert t(body, 5.0) == (200, ok_payload(0.4))
    assert t(body, 5.0) == (200, None)  # 비 JSON → None → call_once 에서 schema
    assert sess.calls[0][0] is sess.calls[1][0]
    _, url, kw = sess.calls[0]
    assert url == "http://127.0.0.1:8009/v1/systemone"
    assert kw["headers"] == {"Authorization": "Bearer local"}
    assert kw["timeout"] == 5.0 and kw["json"] == body
    assert t.server_stats() == {"hits": 3, "misses": 7}


def test_http_transport_default_session_is_requests_session():
    assert isinstance(sp.HttpTransport("http://127.0.0.1:8009").session, requests.Session)


# --- 9. 서버 스크립트 ---------------------------------------------------------------------------------------------


def test_server_script_syntax_binding_and_pins():
    assert subprocess.run(["bash", "-n", str(SERVER_SH)]).returncode == 0
    text = SERVER_SH.read_text()
    assert re.search(r"^HOST=127\.0\.0\.1$", text, re.M)
    assert "0.0.0.0" not in text
    commit = re.search(r'^KEV_COMMIT="([0-9a-f]+)"$', text, re.M).group(1)
    rev = re.search(r'^KEV_HF_REVISION="([0-9a-f]+)"$', text, re.M).group(1)
    assert len(commit) == 40 and len(rev) == 40
    assert commit == sp.KEV_COMMIT and rev == sp.HF_REVISION
    assert '--host "$HOST"' in text
