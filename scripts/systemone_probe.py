"""System One(Kev-0.8B 로컬 서버) J0 실측 하네스 — 지연·마감 초과·오류·결정성·입력 토큰·판별력.

설계: 볼트 design/jev-decision-layer.md 3절·7절 J0, research/jev-open-source-alternatives.md 5절 6항,
태스크 T-20261010-02. 질문은 Q1 `take`(noul) 하나, state 는 합성(사후 성과 미사용)이다.

    .venv/bin/python scripts/systemone_probe.py run --base-url http://127.0.0.1:8009 --out-dir data/out/systemone
    .venv/bin/python scripts/systemone_probe.py check-report --out-dir data/out/systemone

호출은 전송 함수 `transport(body, timeout) -> (status, payload)` 와 시계 주입으로 감싼다. 테스트는 모의 전송으로
네트워크·GPU 없이 집계·판별력·fail-closed 를 검증한다. 실패 호출(연결·타임아웃·HTTP≠200·스키마)은 건너뜀으로
기록되고 하네스는 끝까지 돈다.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import numpy as np
import requests

DEADLINE_MS = 550.0
TIMEOUT_S = 5.0
SEED = 20261010
WARM_SEED = 999
N_BARS = 15
N_WARM = 5
N_LATENCY = 1000
N_DISC = 20                     # 판별력 ①③ 집합 = 지연 state 0–19
N_DET_STATES = 10
N_DET_REPEAT = 10
MODEL = "kev-latest"
DEFAULT_BASE_URL = "http://127.0.0.1:8009"
KEV_COMMIT = "fc4a17e194bca35a48c57211392ec49ea54a94ac"
HF_REVISION = "bf75a6a8848ea6960ff2ed108d9ed44c2941174f"
HF_MODEL = "jaredpalmer/kev-0.8b"
# make_state(Random(SEED), 15) 처음 20개의 정규 JSON(sort_keys) sha256 — 사전조사 스크립트 원본으로도 같은 값을 확인했다
STATES20_SHA256 = "f7795bee4f76acd07f3391b959f7aeb17929da4fe23a70d57806a0120febc107"

Q1 = "Is the proposed mean-reversion entry likely to reach its target before its stop?"
QUESTIONS = {"take": {"type": "noul", "instructions": Q1}}

# 판별력 고정 기준(trial-ledger J0-kev-0.8b)
MIN_PSTDEV = 0.05
MONO_MIN_SPAN = 0.01
INVARIANCE_TOL = 0.01
MAX_OVER_RATIO = 0.05
TRIGGER_Z_LEVELS = (2.0, 2.375, 2.75, 3.125, 3.5)
TREND_EFF_LEVELS = (0.05, 0.2625, 0.475, 0.6875, 0.9)
FUNDING_LABELS = ("negative", "neutral", "positive")

SUMMARY_KEYS = (
    "n_latency", "transport", "base_url", "model", "kev_commit", "hf_revision", "n_errors", "errors_by_reason",
    "p50_ms", "p95_ms", "p99_ms", "over_deadline_ratio", "determinism", "discriminability", "input_tokens",
    "verdict", "started_at", "finished_at",
)

Transport = Callable[[dict, float], "tuple[int, object]"]


def make_state(rng: random.Random, n_bars: int = 15) -> dict:
    direction = rng.choice(["long", "short"])
    sign = -1 if direction == "long" else 1
    z = round(sign * rng.uniform(2.0, 3.5), 2)
    sigma = round(rng.uniform(0.15, 0.9), 2)
    ratio = round(rng.uniform(0.6, 3.0), 2)
    label = "low" if ratio < 0.8 else ("normal" if ratio < 1.5 else "elevated")
    bars = []
    for _ in range(n_bars):
        bars.append(
            {
                "ret_pct": round(rng.gauss(0, sigma / 4), 3),
                "range_pct": round(abs(rng.gauss(0, sigma / 2)) + 0.02, 3),
                "vol_ratio": round(abs(rng.gauss(1.0, 0.5)) + 0.1, 2),
            }
        )
    return {
        "proposed_trade": {
            "direction": direction,
            "style": "mean-reversion",
            "trigger_z": z,
            "window_min": rng.choice([60, 240]),
        },
        "returns_pct": {
            "1m": round(sign * rng.uniform(0, 0.5), 2),
            "5m": round(sign * rng.uniform(0, 1.2), 2),
            "60m": round(sign * rng.uniform(0, 3.0), 2),
            "240m": round(sign * rng.uniform(0, 5.0), 2),
        },
        "volatility": {"sigma_240m_pct": sigma, "sigma_ratio_vs_24h": ratio, "label": label},
        "trend_efficiency_240m": round(rng.uniform(0.05, 0.9), 2),
        "volume_ratio_vs_24h": round(rng.uniform(0.3, 4.0), 2),
        "funding_rate_label": rng.choice(["negative", "neutral", "positive"]),
        "last_15_bars": bars,
    }


def make_states(n: int, seed: int = SEED, n_bars: int = N_BARS) -> list[dict]:
    rng = random.Random(seed)
    return [make_state(rng, n_bars) for _ in range(n)]


def states_sha256(states: list[dict]) -> str:
    return hashlib.sha256(json.dumps(states, sort_keys=True).encode()).hexdigest()


def request_body(state: dict, model: str = MODEL) -> dict:
    return {"state": state, "model": model, "questions": QUESTIONS}


# --- 전송 ---------------------------------------------------------------------------------------------------------


class HttpTransport:
    """requests.Session 하나로 keep-alive. POST {base}/v1/systemone → (status, JSON 또는 None)."""

    name = "http"

    def __init__(self, base_url: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else requests.Session()

    def __call__(self, body: dict, timeout: float) -> tuple[int, object]:
        r = self.session.post(
            self.base_url + "/v1/systemone",
            json=body,
            headers={"Authorization": "Bearer local"},
            timeout=timeout,
        )
        try:
            payload = r.json()
        except ValueError:
            payload = None
        return r.status_code, payload

    def server_stats(self) -> dict | None:
        """GET /v1/models 의 prefix_cache 계수(적중·미스). 응답 본문에는 호출별 적중 여부가 없어 단계 전후 차이로 센다."""
        try:
            r = self.session.get(self.base_url + "/v1/models", timeout=TIMEOUT_S)
            data = r.json()
        except (requests.RequestException, ValueError):
            return None
        found = _find_key(data, "prefix_cache")
        return found if isinstance(found, dict) else None


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = _find_key(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_key(v, key)
            if hit is not None:
                return hit
    return None


@dataclass
class CallResult:
    ok: bool
    reason: str | None
    q1: float | None
    wall_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    server_latency_ms: float | None = None
    model: str | None = None


def _valid_prob(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0.0 <= x <= 1.0


def call_once(transport: Transport, state: dict, *, model: str = MODEL, timeout: float = TIMEOUT_S,
              clock: Callable[[], float] = time.perf_counter) -> CallResult:
    """한 번 호출. 실패는 예외 없이 ok=False·q1=None·사유로 돌려준다(fail-closed = 건너뜀)."""
    body = request_body(state, model)
    t0 = clock()
    try:
        status, payload = transport(body, timeout)
    except requests.Timeout:
        return CallResult(False, "timeout", None, (clock() - t0) * 1000)
    except requests.ConnectionError:
        return CallResult(False, "connection", None, (clock() - t0) * 1000)
    except requests.RequestException:
        return CallResult(False, "request", None, (clock() - t0) * 1000)
    wall = (clock() - t0) * 1000
    if status != 200:
        return CallResult(False, f"http_{status}", None, wall)
    if not isinstance(payload, dict):
        return CallResult(False, "schema", None, wall)
    answers = payload.get("answers")
    take = answers.get("take") if isinstance(answers, dict) else None
    q1 = take.get("noul") if isinstance(take, dict) else None
    if not _valid_prob(q1):
        return CallResult(False, "schema", None, wall)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return CallResult(True, None, float(q1), wall, usage.get("input_tokens"), usage.get("output_tokens"),
                      payload.get("latency_ms"), payload.get("model"))


# --- 집계 ---------------------------------------------------------------------------------------------------------


def latency_stats(results: list[CallResult], deadline_ms: float = DEADLINE_MS) -> dict:
    """분모 = 전체 호출 수. 실패 호출은 건너뜀 = 마감 초과와 같은 결과라 초과에 포함한다.
    분위수는 성공 호출만으로 내고 그 n 을 같이 적는다. 550 정각은 초과가 아니다(> D)."""
    n = len(results)
    ok_walls = [r.wall_ms for r in results if r.ok]
    over_ok = sum(1 for w in ok_walls if w > deadline_ms)
    n_fail = n - len(ok_walls)
    over = over_ok + n_fail
    if ok_walls:
        p50, p95, p99 = (float(np.percentile(ok_walls, q)) for q in (50, 95, 99))
        mx, mean = float(max(ok_walls)), float(statistics.fmean(ok_walls))
    else:
        p50 = p95 = p99 = mx = mean = None
    return {
        "n": n,
        "n_ok": len(ok_walls),
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
        "max_ms": mx,
        "mean_ms": mean,
        "over_deadline": over,
        "over_deadline_ok_only": over_ok,
        "over_deadline_ratio": over / n if n else None,
    }


def error_counts(results: list[CallResult]) -> dict:
    out: dict[str, int] = {}
    for r in results:
        if not r.ok:
            out[r.reason] = out.get(r.reason, 0) + 1
    return dict(sorted(out.items()))


def determinism(groups: dict[int, list[CallResult]]) -> dict:
    """같은 state 반복 호출의 Q1 이 정확히 같은 state 수. 실패 호출이 하나라도 있으면 불일치로 센다."""
    per = {}
    for i, rs in groups.items():
        qs = [r.q1 for r in rs]
        same = all(r.ok for r in rs) and len(set(qs)) == 1
        spread = (max(qs) - min(qs)) if all(q is not None for q in qs) and qs else None
        per[i] = {"match": same, "spread": spread}
    matched = sum(v["match"] for v in per.values())
    return {"matched": matched, "n_states": len(per), "pass": matched == len(per) and len(per) > 0, "per_state": per}


def _monotone(seq: list[float | None]) -> dict:
    if any(q is None for q in seq) or len(seq) < 2:
        return {"values": seq, "direction": None, "span": None, "pass": False}
    diffs = np.diff(seq)
    up, down = bool(np.all(diffs >= 0)), bool(np.all(diffs <= 0))
    span = abs(seq[-1] - seq[0])
    direction = "up" if up and not down else ("down" if down and not up else ("flat" if up and down else "mixed"))
    return {"values": seq, "direction": direction, "span": span, "pass": (up or down) and span >= MONO_MIN_SPAN}


def discriminability(base_q1: list[float | None], mono_z: list[float | None], mono_te: list[float | None],
                     invariance_pairs: list[tuple[float | None, float | None]]) -> dict:
    """① pstdev(state 0–19) ≥ 0.05 · ② trigger_z·trend_efficiency 둘 다 단조+|끝−처음| ≥ 0.01 · ③ 40쌍 모두 |ΔQ1| < 0.01."""
    if base_q1 and all(q is not None for q in base_q1):
        sd = statistics.pstdev(base_q1)
        spread = {"pstdev": sd, "min": min(base_q1), "max": max(base_q1), "n": len(base_q1), "pass": sd >= MIN_PSTDEV}
    else:
        spread = {"pstdev": None, "min": None, "max": None, "n": len(base_q1), "pass": False}
    a, b = _monotone(mono_z), _monotone(mono_te)
    deltas = [abs(x - y) if x is not None and y is not None else None for x, y in invariance_pairs]
    complete = bool(deltas) and all(d is not None for d in deltas)
    inv = {
        "n_pairs": len(deltas),
        "max_abs_delta": max(deltas) if complete else None,
        "n_violations": sum(1 for d in deltas if d is None or d >= INVARIANCE_TOL),
        "pass": complete and all(d < INVARIANCE_TOL for d in deltas),
    }
    mono = {"trigger_z": a, "trend_efficiency_240m": b, "pass": a["pass"] and b["pass"]}
    return {"spread": spread, "monotonic": mono, "invariance": inv, "pass": spread["pass"] and mono["pass"] and inv["pass"]}


def judge(lat: dict, n_errors: int, det: dict, disc: dict) -> dict:
    reasons = []
    if lat["over_deadline_ratio"] is None or lat["over_deadline_ratio"] > MAX_OVER_RATIO:
        reasons.append(f"마감 초과 {_pct(lat['over_deadline_ratio'])} > 5%")
    if n_errors:
        reasons.append(f"오류 {n_errors}건")
    if not det["pass"]:
        reasons.append(f"결정성 {det['matched']}/{det['n_states']}")
    for key, name in (("spread", "① 분산"), ("monotonic", "② 단조성"), ("invariance", "③ 불변성")):
        if not disc[key]["pass"]:
            reasons.append(f"판별력 {name} 미달")
    return {"verdict": "pass" if not reasons else "human_report", "reasons": reasons}


# --- 실행 ---------------------------------------------------------------------------------------------------------


def trigger_z_variant(state: dict, magnitude: float) -> dict:
    s = copy.deepcopy(state)
    sign = -1.0 if s["proposed_trade"]["trigger_z"] < 0 else 1.0
    s["proposed_trade"]["trigger_z"] = sign * magnitude
    return s


def trend_eff_variant(state: dict, value: float) -> dict:
    s = copy.deepcopy(state)
    s["trend_efficiency_240m"] = value
    return s


def funding_variants(state: dict) -> list[tuple[str, dict]]:
    out = []
    for label in FUNDING_LABELS:
        if label != state["funding_rate_label"]:
            s = copy.deepcopy(state)
            s["funding_rate_label"] = label
            out.append((label, s))
    return out


def system_snapshot() -> dict:
    """GPU 단독 조건 기록: load average 와 CPU 상위 5개 프로세스."""
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = None
    try:
        out = subprocess.run(["ps", "-Ao", "pid,pcpu,comm", "-r"], capture_output=True, text=True, timeout=10).stdout
        top = [" ".join(line.split()[:2] + [Path(" ".join(line.split()[2:])).name]) for line in out.splitlines()[1:6]]
    except (OSError, subprocess.SubprocessError):
        top = []
    return {"loadavg": load, "top_cpu": top}


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def run_probe(transport: Transport, *, n_latency: int = N_LATENCY, model: str = MODEL, timeout: float = TIMEOUT_S,
              clock: Callable[[], float] = time.perf_counter, progress: Callable[[str], None] | None = None,
              server_stats: Callable[[], dict | None] | None = None) -> dict:
    """예열 5 → 지연 n_latency(서로 다른 state) → 결정성 10×10 → 단조성 2×5 → 불변성 20×2. 행과 집계를 돌려준다."""
    if n_latency < N_DISC:
        raise ValueError(f"n_latency 는 {N_DISC} 이상")
    rows: list[dict] = []
    cache: dict[str, dict | None] = {}

    def call(phase: str, state: dict, **tags) -> CallResult:
        r = call_once(transport, state, model=model, timeout=timeout, clock=clock)
        rows.append({"phase": phase, **tags, **asdict(r)})
        return r

    def snap(tag: str) -> None:
        cache[tag] = server_stats() if server_stats else None

    def say(msg: str) -> None:
        if progress:
            progress(msg)

    warm_state = make_state(random.Random(WARM_SEED), N_BARS)
    for k in range(N_WARM):
        call("warm", warm_state, k=k)
    say("예열 끝")

    states = make_states(n_latency)
    snap("latency_before")
    lat_results = []
    for i, s in enumerate(states):
        lat_results.append(call("latency", s, i=i))
        if (i + 1) % 100 == 0:
            say(f"지연 {i + 1}/{n_latency}")
    snap("latency_after")

    det_groups: dict[int, list[CallResult]] = {}
    for i in range(N_DET_STATES):
        det_groups[i] = [call("determinism", states[i], i=i, k=k) for k in range(N_DET_REPEAT)]
    snap("determinism_after")
    say("결정성 끝")

    base = states[0]
    mono_z = [call("mono_trigger_z", trigger_z_variant(base, m), i=0, level=m).q1 for m in TRIGGER_Z_LEVELS]
    mono_te = [call("mono_trend_eff", trend_eff_variant(base, v), i=0, level=v).q1 for v in TREND_EFF_LEVELS]
    pairs = []
    for i in range(N_DISC):
        for label, s in funding_variants(states[i]):
            pairs.append((lat_results[i].q1, call("invariance", s, i=i, label=label).q1))
    say("판별력 끝")

    measured = [r for r in (*lat_results, *(x for g in det_groups.values() for x in g))]
    measured_rows = [row for row in rows if row["phase"] != "warm"]
    all_results = [CallResult(**{k: row[k] for k in CallResult.__dataclass_fields__}) for row in measured_rows]
    lat = latency_stats(lat_results)
    det = determinism(det_groups)
    disc = discriminability([r.q1 for r in lat_results[:N_DISC]], mono_z, mono_te, pairs)
    errors = error_counts(all_results)
    n_errors = sum(errors.values())
    toks = [r.input_tokens for r in lat_results if r.ok and r.input_tokens is not None]
    out_toks = [r.output_tokens for r in lat_results if r.ok and r.output_tokens is not None]
    models = sorted({r.model for r in measured if r.model})
    return {
        "rows": rows,
        "latency": lat,
        "errors": errors,
        "n_errors": n_errors,
        "n_calls_measured": len(measured_rows),
        "latency_phase_errors": error_counts(lat_results),
        "determinism": det,
        "discriminability": disc,
        "input_tokens": {"min": min(toks), "mean": statistics.fmean(toks), "max": max(toks)} if toks else None,
        "output_tokens_mean": statistics.fmean(out_toks) if out_toks else None,
        "models": models,
        "prefix_cache": _cache_deltas(cache),
        "judgement": judge(lat, n_errors, det, disc),
    }


def _cache_deltas(cache: dict) -> dict | None:
    def delta(a, b):
        x, y = cache.get(a), cache.get(b)
        if not x or not y:
            return None
        return {k: y.get(k, 0) - x.get(k, 0) for k in ("hits", "misses")}

    if not any(cache.values()):
        return None
    return {"latency": delta("latency_before", "latency_after"), "determinism": delta("latency_after", "determinism_after")}


# --- 리포트 -------------------------------------------------------------------------------------------------------

_REPORT_RE = re.compile(r"^probe_(\d{8})(?:-(\d+))?\.jsonl$")


def _report_key(p: Path) -> tuple[str, int] | None:
    m = _REPORT_RE.match(p.name)
    return (m.group(1), int(m.group(2) or 1)) if m else None


def next_report_stem(out_dir: Path, day: str) -> str:
    """probe_<YYYYMMDD>, 같은 날 재실행이면 -2, -3 … (덮어쓰지 않음)."""
    used = [k[1] for p in out_dir.glob(f"probe_{day}*.jsonl") if (k := _report_key(p)) and k[0] == day]
    used += [k[1] for p in out_dir.glob(f"probe_{day}*.md")
             if (k := _report_key(p.with_suffix(".jsonl"))) and k[0] == day]
    if not used:
        return f"probe_{day}"
    return f"probe_{day}-{max(used) + 1}"


def latest_report(out_dir: Path) -> Path | None:
    """파일명 사전순이 아니라 (날짜, 접미사 정수) 로 최신을 고른다(`-2` < `-10`, 접미사 없음 = 1)."""
    keyed = [(k, p) for p in out_dir.glob("probe_*.jsonl") if (k := _report_key(p))]
    return max(keyed)[1] if keyed else None


def _f(x, nd=1) -> str:
    return "—" if x is None else f"{x:,.{nd}f}"


def _pct(x) -> str:
    return "—" if x is None else f"{100 * x:.1f}%"


def build_summary(res: dict, meta: dict) -> dict:
    lat, disc = res["latency"], res["discriminability"]
    return {
        "n_latency": lat["n"],
        "transport": meta["transport"],
        "base_url": meta["base_url"],
        "model": res["models"][0] if len(res["models"]) == 1 else res["models"],
        "request_model": meta["request_model"],
        "kev_commit": meta["kev_commit"],
        "hf_revision": meta["hf_revision"],
        "deadline_ms": DEADLINE_MS,
        "timeout_s": meta["timeout_s"],
        "n_calls_measured": res["n_calls_measured"],
        "n_errors": res["n_errors"],
        "errors_by_reason": res["errors"],
        "latency_phase_errors": res["latency_phase_errors"],
        "n_ok_latency": lat["n_ok"],
        "p50_ms": lat["p50_ms"],
        "p95_ms": lat["p95_ms"],
        "p99_ms": lat["p99_ms"],
        "max_ms": lat["max_ms"],
        "over_deadline": lat["over_deadline"],
        "over_deadline_ratio": lat["over_deadline_ratio"],
        "determinism": {k: res["determinism"][k] for k in ("matched", "n_states", "pass")},
        "discriminability": {
            "spread_pstdev": disc["spread"]["pstdev"], "spread_pass": disc["spread"]["pass"],
            "mono_trigger_z": disc["monotonic"]["trigger_z"], "mono_trend_eff": disc["monotonic"]["trend_efficiency_240m"],
            "monotonic_pass": disc["monotonic"]["pass"],
            "invariance_max_abs_delta": disc["invariance"]["max_abs_delta"], "invariance_pass": disc["invariance"]["pass"],
            "pass": disc["pass"],
        },
        "input_tokens": res["input_tokens"],
        "output_tokens_mean": res["output_tokens_mean"],
        "prefix_cache": res["prefix_cache"],
        "states20_sha256": meta["states20_sha256"],
        "verdict": res["judgement"]["verdict"],
        "verdict_reasons": res["judgement"]["reasons"],
        "started_at": meta["started_at"],
        "finished_at": meta["finished_at"],
        "env_before": meta["env_before"],
        "env_after": meta["env_after"],
    }


def render_markdown(summary: dict, res: dict) -> str:
    lat, det, disc = res["latency"], res["determinism"], res["discriminability"]
    s = summary
    verdict = "pass" if s["verdict"] == "pass" else "사람 보고 — " + "; ".join(s["verdict_reasons"])
    mz, mt = disc["monotonic"]["trigger_z"], disc["monotonic"]["trend_efficiency_240m"]
    inv = disc["invariance"]
    tok = s["input_tokens"] or {}
    pc = s["prefix_cache"] or {}
    det_bad = [i for i, v in det["per_state"].items() if not v["match"]]
    lines = [
        f"# System One J0 실측 리포트 — {s['started_at'][:10]}",
        "",
        f"**판정: {verdict}**",
        "",
        "## 실행 조건",
        "",
        f"- 서버: `{s['base_url']}` · 전송 `{s['transport']}` (requests.Session keep-alive) · 요청 model `{s['request_model']}` · 응답 model `{s['model']}`",
        f"- Kev 커밋 `{s['kev_commit']}` · HF `{HF_MODEL}@{s['hf_revision']}`",
        f"- 질문: Q1 `take` noul 하나 — \"{Q1}\"",
        f"- state: `make_state(Random({SEED}), {N_BARS})` 차례로 · 처음 20개 sha256 `{s['states20_sha256']}`",
        f"- 마감 D = {DEADLINE_MS:.0f} ms (클라이언트 벽시계 > D 이면 초과) · 요청 타임아웃 {s['timeout_s']} s",
        f"- 실행 {s['started_at']} → {s['finished_at']}",
        f"- 실행 전 load {s['env_before'].get('loadavg')} · CPU 상위 5: {s['env_before'].get('top_cpu')}",
        f"- 실행 후 load {s['env_after'].get('loadavg')} · CPU 상위 5: {s['env_after'].get('top_cpu')}",
        "",
        "## 지연 (서로 다른 state)",
        "",
        "| n | 성공 n | p50 ms | p95 ms | p99 ms | max ms | 마감 초과 | 초과 비율 |",
        "|---|---|---|---|---|---|---|---|",
        f"| {lat['n']:,} | {lat['n_ok']:,} | {_f(lat['p50_ms'])} | {_f(lat['p95_ms'])} | {_f(lat['p99_ms'])} | "
        f"{_f(lat['max_ms'])} | {lat['over_deadline']:,} | {_pct(lat['over_deadline_ratio'])} |",
        "",
        "- 분위수는 성공 호출만으로 낸다. 실패 호출은 건너뜀 = 마감 초과로 본다(분모 = 전체).",
        f"- 서버 prefix 캐시 적중(GET /v1/models 계수 차): 지연 단계 {pc.get('latency')}, 결정성 단계 {pc.get('determinism')}",
        "",
        "## 오류",
        "",
        f"- 범위: 예열 제외 전체 {s['n_calls_measured']:,}회 — 오류 **{s['n_errors']}** {s['errors_by_reason'] or ''}",
        f"- 지연 단계만: {s['latency_phase_errors'] or 0}",
        "",
        "## 결정성",
        "",
        f"- state 0–{N_DET_STATES - 1} × {N_DET_REPEAT}회 연속: Q1 정확히 일치 **{det['matched']}/{det['n_states']}**"
        + (f" (불일치 state {det_bad})" if det_bad else ""),
        "- 서버의 state prefix KV 캐시(KEV_PREFIX_CACHE 기본 4, LRU) 때문에 2회차부터 캐시 적중이다. "
        "즉 '캐시 미스 1회 vs 캐시 적중 9회' 일치를 뜻한다.",
        "",
        "## 입력 토큰",
        "",
        f"- 지연 단계 input_tokens min {tok.get('min')} · mean {_f(tok.get('mean'))} · max {tok.get('max')} · "
        f"output_tokens mean {_f(s['output_tokens_mean'])}",
        "",
        "## 판별력 (고정 기준: trial-ledger `J0-kev-0.8b`)",
        "",
        f"- ① 분산: state 0–19 Q1 pstdev **{_f(disc['spread']['pstdev'], 4)}** (기준 ≥ {MIN_PSTDEV}) · "
        f"min {_f(disc['spread']['min'], 4)} · max {_f(disc['spread']['max'], 4)} → {'충족' if disc['spread']['pass'] else '미달'}",
        f"- ② 단조성 (a) trigger_z {list(TRIGGER_Z_LEVELS)}: Q1 {[_f(v, 4) for v in mz['values']]} · 방향 {mz['direction']} · "
        f"|끝−처음| {_f(mz['span'], 4)} → {'충족' if mz['pass'] else '미달'}",
        f"- ② 단조성 (b) trend_efficiency_240m {list(TREND_EFF_LEVELS)}: Q1 {[_f(v, 4) for v in mt['values']]} · 방향 {mt['direction']} · "
        f"|끝−처음| {_f(mt['span'], 4)} → {'충족' if mt['pass'] else '미달'}",
        f"- ③ 불변성: funding_rate_label {inv['n_pairs']}쌍 · max |ΔQ1| {_f(inv['max_abs_delta'], 4)} (기준 < {INVARIANCE_TOL}) · "
        f"위반 {inv['n_violations']} → {'충족' if inv['pass'] else '미달'}",
        "",
        "## 판정 기준",
        "",
        "마감 초과 ≤ 5% · 오류 0 · 결정성 10/10 · 판별력 ①②③ 모두 충족 → pass. 하나라도 미달이면 사람 보고"
        "(설계 5절 4항·연구 5절 6항). 자동으로 다른 안으로 넘어가지 않는다.",
        "",
    ]
    return "\n".join(lines)


def write_report(out_dir: Path, res: dict, meta: dict, day: str | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = next_report_stem(out_dir, day or dt.date.today().strftime("%Y%m%d"))
    summary = build_summary(res, meta)
    jl, md = out_dir / f"{stem}.jsonl", out_dir / f"{stem}.md"
    with jl.open("w") as f:
        for row in res["rows"]:
            f.write(json.dumps(row) + "\n")
        f.write(json.dumps({"summary": summary}) + "\n")
    md.write_text(render_markdown(summary, res))
    return jl, md


def run_and_report(transport: Transport, out_dir: Path, *, transport_name: str, base_url: str,
                   n_latency: int = N_LATENCY, model: str = MODEL, timeout: float = TIMEOUT_S,
                   clock: Callable[[], float] = time.perf_counter, snapshot: Callable[[], dict] = system_snapshot,
                   server_stats: Callable[[], dict | None] | None = None, day: str | None = None,
                   progress: Callable[[str], None] | None = None) -> tuple[Path, Path, dict]:
    meta = {"transport": transport_name, "base_url": base_url, "request_model": model, "kev_commit": KEV_COMMIT,
            "hf_revision": HF_REVISION, "timeout_s": timeout, "states20_sha256": states_sha256(make_states(N_DISC)),
            "started_at": _now(), "env_before": snapshot()}
    res = run_probe(transport, n_latency=n_latency, model=model, timeout=timeout, clock=clock,
                    progress=progress, server_stats=server_stats)
    meta.update(finished_at=_now(), env_after=snapshot())
    jl, md = write_report(out_dir, res, meta, day)
    return jl, md, build_summary(res, meta)


# --- check-report -------------------------------------------------------------------------------------------------


def _is_loopback(url: str) -> bool:
    return urlparse(url).hostname in ("127.0.0.1", "localhost", "::1")


def check_report(out_dir: Path) -> list[str]:
    """최신 리포트가 실호출 산출물로서 완전한지만 본다(판정 결과와 무관). 문제 목록을 돌려준다(빈 목록 = 정상)."""
    jl = latest_report(out_dir) if out_dir.is_dir() else None
    if jl is None:
        return [f"{out_dir} 에 probe_*.jsonl 없음"]
    problems = []
    if not jl.with_suffix(".md").exists():
        problems.append(f"{jl.with_suffix('.md').name} 없음")
    try:
        last = jl.read_text().strip().splitlines()[-1]
        summary = json.loads(last)["summary"]
    except (IndexError, ValueError, KeyError, TypeError):
        return problems + [f"{jl.name} 마지막 줄이 요약 행이 아님"]
    missing = [k for k in SUMMARY_KEYS if k not in summary]
    if missing:
        problems.append(f"요약 키 누락: {missing}")
    if not isinstance(summary.get("n_latency"), int) or summary["n_latency"] < N_LATENCY:
        problems.append(f"n_latency {summary.get('n_latency')} < {N_LATENCY}")
    if summary.get("transport") != "http":
        problems.append(f"transport {summary.get('transport')!r} ≠ 'http' (실호출 아님)")
    if not isinstance(summary.get("base_url"), str) or not _is_loopback(summary["base_url"]):
        problems.append(f"base_url {summary.get('base_url')!r} 이 loopback 아님")
    if not summary.get("model"):
        problems.append("응답 model 없음")
    if summary.get("kev_commit") != KEV_COMMIT or summary.get("hf_revision") != HF_REVISION:
        problems.append("kev_commit/hf_revision 이 고정값과 다름")
    return problems


# --- CLI ----------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="실호출 실측 후 리포트 작성")
    r.add_argument("--base-url", default=DEFAULT_BASE_URL)
    r.add_argument("--model", default=MODEL)
    r.add_argument("--n-latency", type=int, default=N_LATENCY)
    r.add_argument("--timeout-s", type=float, default=TIMEOUT_S)
    r.add_argument("--out-dir", type=Path, default=Path("data/out/systemone"))
    c = sub.add_parser("check-report", help="최신 리포트 완전성 확인")
    c.add_argument("--out-dir", type=Path, default=Path("data/out/systemone"))
    args = ap.parse_args(argv)

    if args.cmd == "check-report":
        problems = check_report(args.out_dir)
        for p in problems:
            print(f"FAIL {p}", file=sys.stderr)
        if not problems:
            print(f"OK {latest_report(args.out_dir)}")
        return 1 if problems else 0

    if not _is_loopback(args.base_url):
        print(f"base_url 은 loopback 이어야 한다: {args.base_url}", file=sys.stderr)
        return 2
    t = HttpTransport(args.base_url)
    jl, md, summary = run_and_report(
        t, args.out_dir, transport_name=t.name, base_url=args.base_url, n_latency=args.n_latency,
        model=args.model, timeout=args.timeout_s, server_stats=t.server_stats,
        progress=lambda m: print(f"[{_now()}] {m}", flush=True),
    )
    keys = ("n_latency", "p50_ms", "p95_ms", "p99_ms", "over_deadline_ratio", "n_errors", "determinism",
            "discriminability", "verdict", "verdict_reasons")
    print(json.dumps({k: summary[k] for k in keys}, ensure_ascii=False, indent=1))
    print(f"리포트: {md}\n행: {jl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
