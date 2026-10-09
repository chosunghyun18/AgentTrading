"""ui.data — 소형 픽스처 산출물 로드, selection None, OOS 경계 가드, 볼트 태스크 파싱."""

import json
from unittest import mock

import pandas as pd
import pytest

from src.ingest import store
from src.ui import data as ud
from tests import ui_fixtures as fx

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    return fx.build(tmp_path_factory.mktemp("ui"))


@pytest.fixture(scope="module")
def report(tree):
    return ud.load_report(tree["out"], fx.REPORT)


def test_reports_listed_without_selection_file(tree):
    assert ud.list_reports(tree["out"]) == [fx.REPORT]
    assert ud.list_reports(tree["out"] / "nope") == []
    assert ud.load_report(tree["out"], "missing") is None


def test_gate_kpis(report):
    k = {r["key"]: r for r in ud.gate_kpis(report)}
    assert list(k) == ["n_trades", "sharpe", "mdd", "dsr"]
    st = report["stitched"]["default"]
    assert k["n_trades"]["value"] == st["n_trades"] and k["n_trades"]["ok"] == (st["n_trades"] >= 1)
    assert k["mdd"]["op"] == "<=" and k["mdd"]["threshold"] == report["gate"]["max_drawdown"]
    rep = json.loads(json.dumps(report))
    rep["dsr"]["default"] = None  # NaN → 미달
    assert ud.gate_kpis(rep)[3]["ok"] is False


def test_phase4_status_derived_from_verdict(report):
    rep = dict(report, verdict="fail")
    assert ud.phase4_status(rep)["state"] == "blocked" and "fail" in ud.phase4_status(rep)["text"]
    assert ud.phase4_status(dict(report, verdict="pass"))["state"] == "pending"


def test_fold_rows_handles_no_selection(report):
    rep = json.loads(json.dumps(report))
    rep["folds"][0]["selection"] = None
    df = ud.fold_rows(rep)
    assert len(df) == len(rep["folds"])
    assert df.loc[0, "선택"] == ud.NO_SELECTION and pd.isna(df.loc[0, "학습 Sharpe"])
    assert df.loc[1, "트리거"] in ("h1", "h2")


def test_equity_and_wf_trades(tree, report):
    eq = ud.load_equity(tree["out"], fx.REPORT)
    assert set(eq["profile"]) == {"default", "bybit"}
    tr = ud.load_wf_trades(tree["out"], fx.REPORT)
    assert len(tr) == report["stitched"]["default"]["n_trades"]
    assert ud.load_equity(tree["out"], "missing") is None and ud.load_wf_trades(tree["out"], "missing") is None


def test_summaries_and_trigger_ratio(tree):
    s = ud.list_summaries(tree["out"])
    assert set(s["span"]) == {fx.SPAN} and set(s["profile"]) == {"default"}
    meta, runs = ud.load_summary(s["path"].iloc[0])
    assert len(runs) == 4 and set(runs["trigger"]) == {"h1", "h2"}
    r = ud.trigger_pass_ratio(runs)
    assert r["n_runs"].sum() == 4 and ((0 <= r["ratio"]) & (r["ratio"] <= 1)).all()


def test_roundtrips_filtered_by_run(tree):
    spans = ud.list_roundtrip_spans(tree["out"])
    assert spans.to_dict("records") == [{"profile": "default", "span": fx.SPAN}]
    _, runs = ud.load_summary(ud.list_summaries(tree["out"])["path"].iloc[0])
    r = runs[runs["n_trades"] > 0].iloc[0]
    rt = ud.load_roundtrips(tree["out"], "default", fx.SPAN, r["strategy_id"], r["param_id"])
    assert len(rt) == r["n_trades"] and set(rt["param_id"]) == {r["param_id"]}


def test_oos_span_hidden_and_rejected(tree, tmp_path):
    with pytest.raises(ValueError):
        ud.span_range("20211201_20220102")
    with pytest.raises(ValueError):
        ud.load_roundtrips(tree["out"], "default", "20220101_20220201", "syn-v1-h1", "x")
    d = tmp_path / "summary" / "default"
    d.mkdir(parents=True)
    (d / "20220101_20220201.json").write_text("{}", encoding="utf-8")
    assert ud.list_summaries(tmp_path).empty


def test_guard_roundtrips_rejects_oos_entry():
    ok = pd.DataFrame({"entry_ts": [T("2021-12-31 23:59")]})
    assert ud.guard_roundtrips(ok) is ok
    with pytest.raises(ValueError, match="OOS"):
        ud.guard_roundtrips(pd.DataFrame({"entry_ts": [T("2022-01-01")]}))


@pytest.mark.parametrize("start, end, allowed", [
    ("2021-12-31", "2022-01-01", True),    # 표본 마지막 날
    ("2022-01-01", "2022-01-02", False),   # OOS 첫날
    ("2021-12-31", "2022-01-02", False),   # 경계를 넘는 구간
    ("2018-02-28", "2018-03-01", False),   # 표본 앞
])
def test_bars_guard_boundaries(start, end, allowed):
    with mock.patch.object(store, "load_bars", return_value=pd.DataFrame()) as lb:
        if allowed:
            ud.load_bars_guarded("x", "XBTUSD", T(start), T(end))
            lb.assert_called_once()
            assert lb.call_args.args[:2] == (pd.Timestamp(start).date(), pd.Timestamp(start).date())  # 끝 포함 변환
        else:
            with pytest.raises(ValueError):
                ud.load_bars_guarded("x", "XBTUSD", T(start), T(end))
            lb.assert_not_called()


def test_bars_window_clamped_to_sample():
    s, e = ud.bars_window(T("2021-12-31 20:00"), T("2021-12-31 22:00"), 24)
    assert s == T("2021-12-30 20:00") and e == T("2022-01-01")
    with pytest.raises(ValueError):
        ud.bars_window(T("2020-03-01"), T("2020-03-01"), 25)


def test_trade_bars_window(tree):
    tr = ud.load_wf_trades(tree["out"], fx.REPORT).iloc[0]
    bars = ud.load_trade_bars(tree["norm"], "XBTUSD", tr["entry_ts"], tr["exit_ts"], 2)
    assert len(bars) > 0
    assert bars["ts"].min() >= max(tr["entry_ts"] - pd.Timedelta(hours=2), T("2020-03-01"))
    assert bars["ts"].max() <= tr["exit_ts"] + pd.Timedelta(hours=2)


def test_trade_bars_missing_day(tree):
    with pytest.raises(store.MissingDaysError):
        ud.load_trade_bars(tree["norm"], "XBTUSD", T("2020-03-10 01:00"), T("2020-03-10 02:00"), 1)


def test_coverage(tree, tmp_path):
    c = ud.coverage(tree["norm"])
    assert c["n_days"] == 6 and str(c["first"]) == "2020-03-01" and str(c["last"]) == "2020-03-06"
    assert c["sample_missing"] == c["sample_days"] - 6
    assert ud.coverage(tmp_path)["n_days"] == 0


def test_vault_tasks_and_progress(tree, tmp_path):
    t = ud.vault_tasks(tree["vault"])
    assert set(t["id"]) == {"T-1", "T-2", "T-3", "T-4"}
    assert t.loc[t["id"] == "T-4", "status"].item() == "manual"
    p = ud.phase_progress(t).set_index("phase")
    assert p.loc[3, "done"] == 1 and p.loc[3, "total"] == 2 and p.loc[0, "ratio"] == 1.0
    assert ud.vault_tasks(tmp_path).empty and ud.phase_progress(ud.vault_tasks(tmp_path)).empty


def test_equity_stale(tree, tmp_path):
    import os
    import shutil
    out = tmp_path / "out"
    shutil.copytree(tree["out"] / "walkforward", out / "walkforward")
    assert not ud.equity_stale(out, fx.REPORT)
    rep = ud.report_path(out, fx.REPORT)
    t = rep.stat().st_mtime + 100
    os.utime(rep, (t, t))
    assert ud.equity_stale(out, fx.REPORT)
    assert not ud.equity_stale(out, "missing")


def test_bars_single_entry_point():
    """UI 에서 `store.load_bars` 를 부르는 곳은 `load_bars_guarded` 한 곳뿐(OOS 가드 우회 방지)."""
    from pathlib import Path
    ui = Path(__file__).resolve().parents[1] / "src" / "ui"
    hits = [(p.name, line.strip()) for p in ui.rglob("*.py") for line in p.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith(('"""', "#", "`"))
            and any(k in line for k in ("load_bars(", "load_trades(", "bars_1m"))]
    assert hits == [("data.py", "return store.load_bars(s.date(), (e - _DAY).date(), symbol, out_dir=Path(data_dir))")]
