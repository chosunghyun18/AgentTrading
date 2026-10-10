"""진단 화면 집계가 연구 문서 phase3-walkforward-fail-analysis 의 실데이터 수치를 재현하는지(산출물 없으면 skip).

기준값: Obsidian `research/phase3-walkforward-fail-analysis.md` "폴드별"·"트리거별"·"비용·펀딩 기여"·"학습-검증 괴리".
"""

from pathlib import Path

import pytest

from src.ui import data as ud

OUT = Path(__file__).resolve().parents[1] / "data" / "out" / "backtest"
REPORT = ud.load_report(OUT, "default+funding")
DIAG = ud.load_diagnose(OUT, REPORT) if REPORT else None
pytestmark = pytest.mark.skipif(DIAG is None, reason="실데이터 진단 산출물 없음")


def _r3(xs):
    return [round(float(x), 3) for x in xs]


def test_consistency():
    assert ud.diagnose_consistency(DIAG, REPORT)["ok"]


def test_divergence_table():
    d = ud.train_test_divergence(DIAG, REPORT, REPORT["gate"])
    assert _r3(d["Spearman(r1)"]) == [0.938, 0.934, 0.944, 0.876, 0.935, 0.934]
    assert _r3(d["Spearman(후보)"]) == [0.684, 0.575, 0.506, 0.279, 0.416, -0.018]
    assert list(d["후보 수"]) == [135, 108, 97, 85, 34, 21]
    assert [round(x, 2) for x in d["선택 검증 Sharpe"]] == [0.48, 0.49, 2.75, -2.56, 1.34, 0.35]


def test_trigger_gate_counts():
    g = REPORT["gate"]
    tr = ud.trigger_fold_stats(DIAG, "train", g)
    te = ud.trigger_fold_stats(DIAG, "test", g)
    assert list(tr[tr["트리거"] == "h2"]["게이트 충족(r1)"]) == [32, 27, 20, 22, 5, 3]
    assert list(te[te["트리거"] == "h2"]["게이트 충족(r1)"]) == [13, 12, 3, 0, 18, 7]
    assert list(te[te["트리거"] == "h3"]["게이트 충족(r1)"]) == [0, 0, 0, 0, 7, 8]


def test_cost_erosion_totals():
    e = ud.cost_erosion(DIAG).set_index(["구간", "폴드"])
    tr, te = e.loc[("학습", "전체")], e.loc[("검증", "전체")]
    assert round(tr["gross>0"], 3) == 0.665 and round(tr["net>0"], 3) == 0.085
    assert round(tr["gross>0 중 net≤0"], 3) == 0.872 and round(te["gross>0 중 net≤0"], 3) == 0.871
    assert round(te["net>0"], 3) == 0.067
