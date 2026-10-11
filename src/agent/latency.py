"""주문 1건의 단계별 시각(t0~t6) 기록과 p50·p95·p99 집계 — 설계 `jev-decision-layer.md` 5절.

두 시계를 섞지 않는다.

| 단계 | 시계 | 뜻 |
|---|---|---|
| t0 | 거래소(ms) | 1분봉 마감 시각 = 봉 시작 + 60 000 |
| t1 | 단조(ns) | 로컬에서 봉을 닫은 시각(`BarBuilder.poll` 반환) |
| t2 | 단조 | 신호 평가 끝 |
| t3 | 단조 | 게이트(Pass/Jev) 판정 끝 |
| t4 | 단조 | 리스크 검사 끝 |
| t5 | 단조 | 주문 송신 직전 |
| t5r | 단조 | REST 응답 수신 |
| t6_new | 거래소 | `order` 스트림 `orderStatus=New` 의 `updatedTime` |
| t6_exec | 거래소 | `execution` 스트림 첫 체결 `execTime` |

지표: 결정 지연 = t5 − t1(단조), 접수 응답 = t5r − t5(단조), 체결 지연 = t6_exec − t0(거래소),
대기 성립 지연 = t6_new − t0(거래소, 지정가만), PostOnly 거부율. 두 시계를 섞은 값은 `ref_` 로 따로 둔다.
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MONO_STAGES = ("t1", "t2", "t3", "t4", "t5", "t5r")
EXCH_STAGES = ("t6_new", "t6_exec")
METRICS = ("decision_ms", "ack_ms", "fill_ms", "new_ms")


@dataclass
class Trace:
    key: str
    t0_ms: int
    path: str = "base"        # base(PassGate) | jev
    kind: str = ""            # market | ioc | postonly
    mono_ns: dict[str, int] = field(default_factory=dict)
    exch_ms: dict[str, int] = field(default_factory=dict)
    ref: dict[str, float] = field(default_factory=dict)
    outcome: str = ""         # filled | new | postonly_rejected | rejected | skipped | unknown
    _clock: Callable[[], int] = field(default=time.monotonic_ns, repr=False)

    def mark(self, stage: str) -> None:
        if stage not in MONO_STAGES:
            raise ValueError(f"단조 시계 단계가 아니다: {stage}")
        self.mono_ns.setdefault(stage, self._clock())

    def exch(self, stage: str, ms: int) -> None:
        if stage not in EXCH_STAGES:
            raise ValueError(f"거래소 시계 단계가 아니다: {stage}")
        self.exch_ms.setdefault(stage, int(ms))

    def metrics(self) -> dict[str, float | None]:
        m, e = self.mono_ns, self.exch_ms
        mono = lambda a, b: (m[b] - m[a]) / 1e6 if a in m and b in m else None  # noqa: E731
        return {"decision_ms": mono("t1", "t5"), "ack_ms": mono("t5", "t5r"),
                "fill_ms": e["t6_exec"] - self.t0_ms if "t6_exec" in e else None,
                "new_ms": e["t6_new"] - self.t0_ms if "t6_new" in e else None}

    def record(self) -> dict:
        return {"key": self.key, "path": self.path, "kind": self.kind, "t0_ms": self.t0_ms, "outcome": self.outcome,
                "mono_ns": self.mono_ns, "exch_ms": self.exch_ms, **self.metrics(),
                **{f"ref_{k}": v for k, v in self.ref.items()}}


class LatencyRecorder:
    """진행 중 추적을 들고 있다가 `finish` 때 jsonl 1행(append·flush)."""

    def __init__(self, path: Path, clock: Callable[[], int] = time.monotonic_ns):
        self.path, self._clock = Path(path), clock
        self.open: dict[str, Trace] = {}

    def start(self, key: str, t0_ms: int, path: str = "base", kind: str = "") -> Trace:
        tr = Trace(key, int(t0_ms), path, kind, _clock=self._clock)
        self.open[key] = tr
        return tr

    def finish(self, key: str, outcome: str) -> dict | None:
        tr = self.open.pop(key, None)
        if tr is None:
            return None
        tr.outcome = outcome
        rec = tr.record()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
        return rec


def read_records(path: Path) -> list[dict]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # 비정상 종료로 잘린 행
    return out


def _pct(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "p99": None}
    a = np.asarray(xs, dtype=float)
    p50, p95, p99 = np.percentile(a, [50, 95, 99])
    return {"n": len(xs), "p50": float(p50), "p95": float(p95), "p99": float(p99)}


def summarize(records: Iterable[dict]) -> dict:
    """(경로, 주문 종류)별 지표 분포 + PostOnly 거부율."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[f"{r.get('path', 'base')}/{r.get('kind', '')}"].append(r)
    out = {}
    for g, rs in sorted(groups.items()):
        s = {m: _pct([r[m] for r in rs if r.get(m) is not None]) for m in METRICS}
        po = [r for r in rs if r.get("kind") == "postonly" and r.get("outcome") in ("new", "filled", "postonly_rejected")]
        s["postonly_reject_rate"] = (sum(r["outcome"] == "postonly_rejected" for r in po) / len(po)) if po else None
        s["outcomes"] = dict(sorted(Counter(r.get("outcome", "") for r in rs).items()))
        out[g] = s
    return out
