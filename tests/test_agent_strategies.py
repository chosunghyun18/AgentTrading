"""agent.strategies — 레지스트리 형식, 관문 순서 규칙, 볼트 설계 문서 상태 일치, v1 파라미터 문장화."""

import os
from pathlib import Path

import pytest
import yaml

from src.agent import strategies as sg

VAULT = Path(os.environ.get("AT_VAULT_DIR") or
             Path.home() / "Documents" / "Obsidian Vault" / "Projects" / "work" / "AgentTrading")


def test_registry_shape():
    ids = [s.id for s in sg.registry()]
    assert len(ids) == len(set(ids))
    for s in sg.registry():
        assert s.status in sg.STATUSES
        assert set(s.steps) == set(sg.STEPS), s.id
        assert set(s.steps.values()) <= set(sg.STEP_STATES), s.id
        assert set(s.notes) <= set(sg.STEPS), s.id
        assert s.entry and s.exit and s.sizing and s.summary and s.evidence and s.doc


def test_step_order_is_consistent():
    """워크포워드 이후 관문(워크포워드·OOS·페이퍼·실거래)을 통과하려면 앞 단계가 모두 완료여야 한다.

    설계·근거·구현 사이는 이력 그대로 둔다 — v1 은 '근거 검증' 원칙(2026-10-09) 전에 구현·판정됐다.
    """
    gates = sg.STEPS[sg.STEPS.index("워크포워드"):]
    for s in sg.registry():
        for step in gates:
            if s.steps[step] == "done":
                prior = sg.STEPS[:sg.STEPS.index(step)]
                assert all(s.steps[p] == "done" for p in prior), f"{s.id}: {step} 완료인데 앞 단계 미완료"


def test_active_requires_all_gates():
    assert sg.active() is None  # 2026-10-09: 관문을 모두 통과한 전략 없음
    for s in sg.registry():
        if s.steps["실거래"] == "done":
            assert all(v == "done" for v in s.steps.values())


def test_status_matches_evidence_rule():
    """사용자 원칙: 근거 데이터 검증이 미달인 전략은 운용·다음 시도 후보가 될 수 없다."""
    for s in sg.registry():
        if s.steps["근거 데이터 검증"] == "fail":
            assert s.status in ("rejected", "on_hold", "discarded"), s.id


@pytest.mark.skipif(not VAULT.is_dir(), reason="볼트 없음")
def test_registry_matches_vault_docs():
    """볼트가 기준 — 설계 문서가 있고 frontmatter status 가 레지스트리와 같아야 한다."""
    for s in sg.registry():
        p = VAULT / s.doc
        assert p.is_file(), f"{s.id}: 볼트 문서 없음 {p}"
        text = p.read_text(encoding="utf-8")
        fm = yaml.safe_load(text.split("---", 2)[1]) if text.startswith("---") else {}
        assert fm.get("status") == s.doc_status, f"{s.id}: 볼트 status {fm.get('status')!r} ≠ 레지스트리 {s.doc_status!r}"


def test_describe_v1():
    out = sg.describe_v1("k=2.5;max_hold=1440;n=240;risk_pct=1;stop_pct=1;tp_r=3;trigger=h2")
    assert out[0] == "진입: 직전 240분 수익률이 같은 기간 변동성의 2.5배를 넘으면 그 방향으로 진입 (모멘텀)"
    assert "손절: 진입가 대비 1% 반대로 가면 청산" in out
    assert "익절: 진입가 대비 3% (3R)" in out
    assert "최대 보유: 1440분 (24시간)" in out
    h1 = sg.describe_v1("max_hold=60;n=15;risk_pct=2;stop_pct=0.5;tp_r=none;trigger=h1")
    assert "돌파" in h1[0] and "익절: 없음" in h1 and "최대 보유: 60분 (1시간)" in h1


def test_blocking_step():
    c1 = next(s for s in sg.registry() if s.id == "C1")
    assert sg.blocking_step(c1) == ("워크포워드", "fail")  # 2026-10-10 시도 2 판정 fail
