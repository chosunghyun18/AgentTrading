"""사용자 대시보드 읽기 계층: `data/out/` 산출물·정규화 1분봉·볼트 태스크를 읽는 순수 함수(Streamlit 무관).

읽기 전용이다 — 어떤 파일도 쓰지 않는다. 1분봉·라운드트립은 기본 표본 [2018-03-01, 2022-01-01) 밖(OOS)을
읽지 않는다: 1분봉은 `load_bars_guarded` 한 곳에서 `check_sample_range` 를 거치고, 라운드트립은 구간 이름과
`entry_ts` 를 표본 가드로 확인한다. OOS 접근 로그는 쓰지 않는다.
설계 근거(단일 기준): Obsidian `Projects/work/AgentTrading/design/user-dashboard.md` "데이터 계약".
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import yaml

from src.backtest.export_equity import export_paths
from src.backtest.walkforward import OOS_START, SAMPLE_START, check_sample_range
from src.ingest import store

PROFILES = ("default", "bybit")
SPAN_RE = re.compile(r"^(\d{8})_(\d{8})$")
NO_SELECTION = "선택 없음"
MAX_PAD_HOURS = 24
PHASE_RE = re.compile(r"Phase (\d)")
_DAY = pd.Timedelta(days=1)


# 워크포워드 판정 ---------------------------------------------------------------------------------------

def list_reports(out_dir: Path) -> list[str]:
    """`walkforward/<name>.json` 판정 리포트 이름(선택 파일 `selection*` 제외), 정렬."""
    d = Path(out_dir) / "walkforward"
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json") if not p.stem.startswith("selection"))


def report_path(out_dir: Path, name: str) -> Path:
    return Path(out_dir) / "walkforward" / f"{name}.json"


def load_report(out_dir: Path, name: str) -> dict | None:
    p = report_path(out_dir, name)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _num(v) -> float:
    return float("nan") if v is None else float(v)


def gate_kpis(report: Mapping, profile: str = "default") -> list[dict]:
    """KPI 4개(거래 수·Sharpe·MDD·DSR) vs 리포트 `gate` 기준. `ok` = 기준 충족(NaN 은 미달)."""
    g, st = report["gate"], report["stitched"][profile]
    dsr = _num((report.get("dsr") or {}).get(profile))
    rows = [
        ("n_trades", "검증 거래 수", _num(st["n_trades"]), g["min_trades"], ">="),
        ("sharpe", "Sharpe", _num(st["sharpe"]), g["min_sharpe"], ">="),
        ("mdd", "MDD", _num(st["mdd"]), g["max_drawdown"], "<="),
        ("dsr", "DSR", dsr, g.get("min_dsr", 0.95), ">="),
    ]
    out = []
    for key, label, v, th, op in rows:
        ok = math.isfinite(v) and (v >= th if op == ">=" else v <= th)
        out.append({"key": key, "label": label, "value": v, "threshold": th, "op": op, "ok": ok})
    return out


def phase4_status(report: Mapping) -> dict:
    """Phase 4 착수 상태 — 판정(`verdict`)에서 파생. 통과여도 OOS 1회 판정·사람 승인 전에는 착수하지 않는다(Spec 1·3절)."""
    if report.get("verdict") == "pass":
        return {"state": "pending", "text": "워크포워드 통과 — OOS 1회 판정과 사람 승인 뒤 Phase 4 착수 (Spec 3절)"}
    return {"state": "blocked", "text": f"워크포워드 게이트 {report.get('verdict')} — Phase 4 착수 금지 (Spec 1·3절)"}


def _trigger(param_id: str | None) -> str:
    m = re.search(r"(?:^|;)trigger=(\w+)", param_id or "")
    return m.group(1) if m else ""


def fold_rows(report: Mapping) -> pd.DataFrame:
    """폴드 표. `selection` 이 None 인 폴드는 선택·학습 지표를 "선택 없음"/NaN 으로 둔다."""
    rows = []
    for i, f in enumerate(report["folds"], 1):
        sel, d, b = f["selection"], f["test"]["default"], f["test"]["bybit"]
        rows.append({
            "폴드": i,
            "학습": f"{f['train_start']} ~ {f['train_end']}",
            "검증": f"{f['test_start']} ~ {f['test_end']}",
            "트리거": _trigger(sel["param_id"]) if sel else "",
            "선택": sel["param_id"] if sel else NO_SELECTION,
            "학습 Sharpe": _num(sel["train_sharpe"]) if sel else float("nan"),
            "검증 Sharpe": _num(d["sharpe"]),
            "검증 거래": int(d["n_trades"]),
            "검증 MDD": _num(d["mdd"]),
            "default net": _num(d["total_net_ret"]),
            "bybit net": _num(b["total_net_ret"]),
            "판정": d["gate"],
        })
    return pd.DataFrame(rows)


def load_equity(out_dir: Path, name: str) -> pd.DataFrame | None:
    """`export_equity` 산출 일별 자본곡선. 없으면 None."""
    p = export_paths(report_path(out_dir, name))["equity"]
    return pd.read_parquet(p) if p.is_file() else None


def equity_stale(out_dir: Path, name: str) -> bool:
    """자본곡선·거래 파일이 리포트보다 오래됐거나 둘 중 하나만 있으면 True(리포트를 다시 만든 뒤 재산출 안 함)."""
    rp = report_path(out_dir, name)
    files = [p for p in export_paths(rp).values() if p.is_file()]
    if not files or not rp.is_file():
        return False
    return len(files) == 1 or min(p.stat().st_mtime for p in files) < rp.stat().st_mtime


def load_wf_trades(out_dir: Path, name: str) -> pd.DataFrame | None:
    """이어 붙인 검증 라운드트립(default). 없으면 None, 표본 밖 `entry_ts` 가 있으면 `ValueError`."""
    p = export_paths(report_path(out_dir, name))["trades"]
    return guard_roundtrips(pd.read_parquet(p)) if p.is_file() else None


# run 요약·라운드트립 ----------------------------------------------------------------------------------

def span_range(span: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """`YYYYMMDD_YYYYMMDD`(끝 반열린) → 표본 가드를 통과한 UTC 자정 (start, end). 형식 오류·표본 밖 `ValueError`."""
    m = SPAN_RE.match(span)
    if not m:
        raise ValueError(f"구간 이름 형식이 아니다: {span!r}")
    return check_sample_range(pd.Timestamp(m.group(1), tz="UTC"), pd.Timestamp(m.group(2), tz="UTC"))


def _in_sample(span: str) -> bool:
    try:
        span_range(span)
    except ValueError:
        return False
    return True


def list_summaries(out_dir: Path) -> pd.DataFrame:
    """`summary/<profile>/<span>.json` 목록(표본 안 구간만) — 열 `profile`·`span`·`path`."""
    d = Path(out_dir) / "summary"
    rows = [{"profile": p.parent.name, "span": p.stem, "path": p}
            for p in sorted(d.glob("*/*.json")) if _in_sample(p.stem)] if d.is_dir() else []
    return pd.DataFrame(rows, columns=["profile", "span", "path"])


def load_summary(path: Path) -> tuple[dict, pd.DataFrame]:
    """(meta, runs) — runs 에 `trigger` 열을 붙인다."""
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    runs = pd.DataFrame(obj["runs"])
    runs.insert(2, "trigger", [_trigger(p) for p in runs["param_id"]])
    for c in ("sharpe", "mdd", "total_net_ret", "total_gross_ret"):
        if c in runs:
            runs[c] = pd.to_numeric(runs[c], errors="coerce")
    return obj["meta"], runs


def trigger_pass_ratio(runs: pd.DataFrame) -> pd.DataFrame:
    """트리거별 run 수·게이트 통과 수·비율."""
    g = runs.groupby("trigger", sort=True)["gate"]
    out = pd.DataFrame({"n_runs": g.size(), "n_pass": g.apply(lambda s: int((s == "pass").sum()))}).reset_index()
    out["ratio"] = out["n_pass"] / out["n_runs"]
    return out


def list_roundtrip_spans(out_dir: Path) -> pd.DataFrame:
    """라운드트립 산출물이 있는 (profile, span) — 표본 안 구간만. 열 `profile`·`span`."""
    d = Path(out_dir) / "roundtrips_net"
    pairs = {(p.parent.parent.name, p.stem) for p in d.glob("*/*/*.parquet")} if d.is_dir() else set()
    rows = sorted((pr, sp) for pr, sp in pairs if _in_sample(sp))
    return pd.DataFrame(rows, columns=["profile", "span"])


def guard_roundtrips(df: pd.DataFrame) -> pd.DataFrame:
    """`entry_ts` 가 하나라도 기본 표본 밖이면 `ValueError`(OOS 거래를 화면에 올리지 않는다)."""
    if len(df) and (bool((df["entry_ts"] < SAMPLE_START).any()) or bool((df["entry_ts"] >= OOS_START).any())):
        raise ValueError("기본 표본 밖 거래가 있다 — OOS 는 UI 에서 읽지 않는다")
    return df


def load_roundtrips(out_dir: Path, profile: str, span: str, strategy_id: str, param_id: str) -> pd.DataFrame:
    """run 1개의 라운드트립(구간 파일에서 `param_id` 로 필터). 구간은 표본 가드를 먼저 거친다."""
    span_range(span)
    p = Path(out_dir) / "roundtrips_net" / profile / strategy_id / f"{span}.parquet"
    if not p.is_file():
        raise FileNotFoundError(p)
    df = pq.read_table(p, filters=[("param_id", "=", param_id)]).to_pandas()
    return guard_roundtrips(df.sort_values("trade_id", kind="stable", ignore_index=True))


# 1분봉 --------------------------------------------------------------------------------------------------

def load_bars_guarded(data_dir: Path, symbol: str, start, end_exclusive) -> pd.DataFrame:
    """UI 의 유일한 1분봉 로더. 반열린 `[start, end_exclusive)`(UTC 자정) 표본 가드 → `store.load_bars(끝 포함)`.

    표본 밖(OOS 포함)은 데이터를 읽기 전에 `ValueError`, 결측 일은 `store.MissingDaysError`.
    """
    s, e = check_sample_range(start, end_exclusive)
    return store.load_bars(s.date(), (e - _DAY).date(), symbol, out_dir=Path(data_dir))


def bars_window(entry_ts, exit_ts, pad_hours: float) -> tuple[pd.Timestamp, pd.Timestamp]:
    """거래 전후 `pad_hours`(0~24) 창 [start, end) — 표본 경계에서 자른다."""
    if not 0 <= pad_hours <= MAX_PAD_HOURS:
        raise ValueError(f"pad_hours 는 0~{MAX_PAD_HOURS}: {pad_hours}")
    pad = pd.Timedelta(hours=pad_hours)
    start = max(pd.Timestamp(entry_ts) - pad, SAMPLE_START)
    end = min(pd.Timestamp(exit_ts) + pad + pd.Timedelta(minutes=1), OOS_START)
    return start, end


def load_trade_bars(data_dir: Path, symbol: str, entry_ts, exit_ts, pad_hours: float) -> pd.DataFrame:
    """거래 1건 전후 창의 1분봉(창을 덮는 UTC 일만 읽는다)."""
    start, end = bars_window(entry_ts, exit_ts, pad_hours)
    day0 = start.floor("D")
    day1 = (end - pd.Timedelta(minutes=1)).floor("D") + _DAY
    bars = load_bars_guarded(data_dir, symbol, day0, day1)
    return bars[(bars["ts"] >= start) & (bars["ts"] < end)].reset_index(drop=True)


# 데이터 커버리지 ----------------------------------------------------------------------------------------

def coverage(data_dir: Path, symbol: str = "XBTUSD") -> dict:
    """정규화 manifest 기준 일 수·첫날·끝날, 기본 표본 결측 일 수. manifest 없으면 n_days 0."""
    p = Path(data_dir) / "_manifest" / f"{symbol}.json"
    days = sorted(json.loads(p.read_text(encoding="utf-8"))) if p.is_file() else []
    out = {"symbol": symbol, "n_days": len(days), "first": None, "last": None}
    if days:
        out["first"], out["last"] = (date(int(d[:4]), int(d[4:6]), int(d[6:])) for d in (days[0], days[-1]))
    sample = pd.date_range(SAMPLE_START, OOS_START, freq="D", inclusive="left").strftime("%Y%m%d")
    have = set(days)
    out["sample_days"] = len(sample)
    out["sample_missing"] = sum(1 for d in sample if d not in have)
    return out


# 볼트 태스크(읽기 전용) ---------------------------------------------------------------------------------

def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    try:
        fm = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return fm if isinstance(fm, dict) else {}


def vault_tasks(vault_dir: Path) -> pd.DataFrame:
    """볼트 `task/autodev/*.md` frontmatter → `id`·`title`·`status`·`kind`·`phase`(제목의 "Phase N", 없으면 None)."""
    d = Path(vault_dir) / "task" / "autodev"
    rows = []
    for p in sorted(d.glob("*.md")) if d.is_dir() else []:
        try:
            fm = _frontmatter(p.read_text(encoding="utf-8"))
        except OSError:
            continue
        if fm.get("type") != "autodev-task":
            continue
        title = str(fm.get("title", p.stem))
        m = PHASE_RE.search(title)
        rows.append({"id": str(fm.get("id", "")), "title": title, "status": str(fm.get("status", "")),
                     "kind": str(fm.get("kind", "")), "phase": int(m.group(1)) if m else None})
    return pd.DataFrame(rows, columns=["id", "title", "status", "kind", "phase"])


def phase_progress(tasks: pd.DataFrame) -> pd.DataFrame:
    """Phase 별 태스크 완료 수/전체(제목에 "Phase N" 이 있는 태스크만)."""
    t = tasks.dropna(subset=["phase"])
    if t.empty:
        return pd.DataFrame(columns=["phase", "done", "total", "ratio"])
    g = t.groupby("phase")["status"]
    out = pd.DataFrame({"done": g.apply(lambda s: int((s == "done").sum())), "total": g.size()}).reset_index()
    out["phase"] = out["phase"].astype(int)
    out["ratio"] = out["done"] / out["total"]
    return out
