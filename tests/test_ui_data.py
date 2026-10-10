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


# 진단(B) — 손으로 만든 소형 진단 프레임 ---------------------------------------------------------------------

DG_GATE = {"min_trades": 10, "min_sharpe": 1.0, "max_drawdown": 0.3}
_RUNS = [("a1", "h1", 1.0), ("a2", "h1", 1.0), ("b1", "h2", 1.0), ("b2", "h2", 1.0), ("b3", "h2", 2.0)]
_TRAIN = [(20, 2.0, 0.1, 0.5, 0.2), (5, 3.0, 0.1, 0.3, -0.1), (20, 0.5, 0.2, 0.2, -1.0),
          (20, float("nan"), 0.1, -0.1, -0.2), (30, 1.5, 0.5, 0.4, 0.1)]
_TEST = [(10, 0.5, 0.2, 0.1, 0.05), (10, 1.0, 0.1, 0.1, 0.02), (10, -1.0, 0.3, -0.1, -0.2),
         (10, 2.0, 0.1, 0.3, 0.2), (10, 0.0, 0.1, 0.0, -0.01)]


def _diag():
    rows = []
    for phase, vals in (("train", _TRAIN), ("test", _TEST)):
        for (pid, trig, risk), (n, sh, mdd, gross, net) in zip(_RUNS, vals):
            rows.append({"fold": 1, "phase": phase, "strategy_id": "s", "param_id": pid, "trigger": trig,
                         "risk_pct": risk, "n_trades": n, "sharpe": sh, "sr_daily": 0.0, "mdd": mdd,
                         "total_gross_ret": gross, "total_net_ret": net, "total_funding_xbt": float("nan")})
    return pd.DataFrame(rows)


def _dg_report(sel="a1", sharpe=0.5):
    return {"gate": DG_GATE, "meta": {},
            "folds": [{"selection": {"strategy_id": "s", "param_id": sel} if sel else None,
                       "test": {"default": {"n_trades": 10, "sharpe": sharpe, "total_net_ret": 0.05}}}]}


def test_diagnose_file_follows_report_funding(tmp_path):
    assert ud.diagnose_file(tmp_path, {"meta": {"funding": {"n": 1}}}).name == "folds+funding.parquet"
    assert ud.diagnose_file(tmp_path, {"meta": {}}).name == "folds.parquet"
    assert ud.load_diagnose(tmp_path, {"meta": {}}) is None


def test_diagnose_consistency():
    d = _diag()
    assert ud.diagnose_consistency(d, _dg_report())["ok"]
    assert ud.diagnose_consistency(d, _dg_report(sel=None))["ok"]  # 선택 없음 폴드는 건너뜀
    assert not ud.diagnose_consistency(d, _dg_report(sharpe=0.5 + 1e-6))["ok"]
    assert "없다" in ud.diagnose_consistency(d, _dg_report(sel="zz"))["reason"]
    two = _dg_report()
    two["folds"].append(two["folds"][0])
    assert "폴드" in ud.diagnose_consistency(d, two)["reason"]


def test_trigger_fold_stats():
    s = ud.trigger_fold_stats(_diag(), "train", DG_GATE).set_index("트리거")
    h1, h2 = s.loc["h1"], s.loc["h2"]
    assert h1["run(r1)"] == 2 and h1["Sharpe 중앙(r1)"] == 2.5 and h1["net>0(r1)"] == 0.5
    assert h1["게이트 충족(r1)"] == 1 and h1["선택 후보(전 run)"] == 1 and h1["소진(r1)"] == 0.0
    assert h2["Sharpe 중앙(r1)"] == 0.5  # NaN 제외
    assert h2["net>0(r1)"] == 0.0 and h2["소진(r1)"] == 0.5 and h2["게이트 충족(r1)"] == 0
    assert h2["선택 후보(전 run)"] == 1  # b1 만(b2 NaN·b3 MDD 초과)


def test_spearman_ties_and_nan():
    a, b = pd.Series([1.0, 1.0, 2.0]), pd.Series([1.0, 2.0, 3.0])
    assert ud._spearman(a, b) == pytest.approx(1.5 / 3 ** 0.5)
    assert pd.isna(ud._spearman(pd.Series([1.0, float("nan")]), pd.Series([1.0, 2.0])))


def test_train_test_divergence():
    r = ud.train_test_divergence(_diag(), _dg_report(), DG_GATE).iloc[0]
    assert r["Spearman(r1)"] == pytest.approx(1.0) and r["후보 수"] == 2 and r["Spearman(후보)"] == pytest.approx(1.0)
    assert r["선택"] == "a1" and r["선택 학습 Sharpe"] == 2.0 and r["선택 검증 Sharpe"] == 0.5 and r["하락폭"] == 1.5
    assert r["검증 백분위(r1)"] == 0.25  # r1 검증 [0.5, 1.0, -1.0, 2.0] 중 0.5 보다 작은 것(strict)
    assert r["검증 백분위(후보)"] == 0.5
    none = ud.train_test_divergence(_diag(), _dg_report(sel=None), DG_GATE).iloc[0]
    assert none["선택"] == ud.NO_SELECTION and pd.isna(none["하락폭"])


def test_cost_erosion():
    e = ud.cost_erosion(_diag())
    assert list(e["폴드"]) == ["F1", "전체", "F1", "전체"] and list(e["구간"]) == ["학습", "학습", "검증", "검증"]
    tr = e.iloc[0]
    assert tr["run(r1)"] == 4 and tr["gross>0"] == 0.75 and tr["net>0"] == 0.25
    assert tr["gross>0 중 net≤0"] == pytest.approx(2 / 3)
    assert pd.isna(tr["펀딩 XBT 중앙"])  # 펀딩 끔 → 미반영
    assert e.iloc[1].drop("폴드").equals(tr.drop("폴드"))  # 폴드 1개면 전체 = F1


def test_fixture_diagnose_matches_report(tree, report):
    d = ud.load_diagnose(tree["out"], report)
    assert d is not None and ud.diagnose_consistency(d, report)["ok"]
