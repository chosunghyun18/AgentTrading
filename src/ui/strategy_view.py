"""전략·관문 페이지 HTML 조각 — 관문 진행표·상태 배지·관문 설명(Streamlit 무관, 단위 테스트 대상)."""

from __future__ import annotations

import html
from dataclasses import dataclass
from urllib.parse import urlencode

from src.agent.strategies import STATUSES, STEP_KEYS, STEPS, Strategy
from src.ui import theme

STATE_STYLE = {  # 상태 → (표시, 배경, 글자) — 토큰 v3(CSS 변수, 라이트·다크 자동)
    "done": ("완료", theme.PROFIT, "#fff"),
    "fail": ("미달", theme.LOSS, "#fff"),
    "running": ("진행 중", theme.PRIMARY, "#fff"),
    "pending": ("대기", theme.BORDER, theme.TEXT),
    "na": ("—", theme.MAIN_BG, theme.INFO),
}
STATUS_COLORS = {"success": theme.SUCCESS, "primary": theme.PRIMARY, "danger": theme.DANGER,
                 "warning": theme.WARNING, "info": theme.INFO}

CSS = f"""
<style>
.pipe {{ width: 100%; table-layout: fixed; border-collapse: separate; border-spacing: 4px; font-size: 13px; }}
.pipe th.name {{ width: 24%; }}
.pipe th {{ color: {theme.TEXT_SECONDARY}; font-weight: 600; text-align: center; padding: 4px; }}
.pipe th.name, .pipe td.name {{ text-align: left; white-space: nowrap; }}
.pipe td {{ text-align: center; padding: 6px 4px; border-radius: 4px; }}
.pipe a {{ color: inherit; text-decoration: none; display: block; }}
.pipe th a:hover {{ text-decoration: underline; }}
.pipe td a:hover {{ filter: brightness(1.15); }}
.pipe .note {{ text-align: left; }}
.gate-row-hit td {{ outline: 2px solid {theme.PRIMARY}; }}
.agent-now {{ font-size: 26px; font-weight: 700; color: {theme.TEXT}; margin: 4px 0 8px; }}
.rule-h {{ color: {theme.TEXT_SECONDARY}; font-size: 13px; font-weight: 600; margin-top: 8px; }}
</style>
"""


def status_badge(s: Strategy) -> str:
    label, color = STATUSES[s.status]
    return theme.badge_html(label, STATUS_COLORS[color])


def gate_href(step: str, strategy_id: str | None = None) -> str:
    """관문 설명 페이지 링크(같은 앱 안 상대 경로 — 하위 경로 배포에서도 동작)."""
    q = {"gate": STEP_KEYS[step]} | ({"strategy": strategy_id} if strategy_id else {})
    return "gates?" + urlencode(q)


def _link(text: str, href: str) -> str:
    return f'<a href="{html.escape(href, quote=True)}" target="_self">{text}</a>'


def pipeline_html(strategies) -> str:
    """전략 × 관문 표. 셀 title 에 단계별 사유(notes), 머리글·칸을 누르면 관문 설명 페이지로 간다."""
    head = "".join(f"<th>{_link(html.escape(st), gate_href(st))}</th>" for st in STEPS)
    rows = []
    for s in strategies:
        cells = []
        for step in STEPS:
            text, bg, fg = STATE_STYLE[s.steps.get(step, "na")]
            note = html.escape(s.notes.get(step, ""), quote=True)
            cells.append(f'<td style="background:{bg};color:{fg}" title="{note}">{_link(text, gate_href(step, s.id))}</td>')
        rows.append(f'<tr class="pipe-row"><td class="name"><b>{html.escape(s.id)}</b> {status_badge(s)}</td>'
                    + "".join(cells) + "</tr>")
    return f'<table class="pipe"><tr><th class="name">전략</th>{head}</tr>{"".join(rows)}</table>'


def bullets(items) -> str:
    return "\n".join(f"- {x}" for x in items)


# 관문 설명 — 기준 문구는 볼트 문서를 옮긴 것(새 기준을 만들지 않는다). 설계: `task/ui-gate-detail-page.md`


@dataclass(frozen=True)
class Gate:
    step: str
    what: str                   # 이 관문이 보는 것
    criteria: tuple[str, ...]   # 통과 기준
    docs: tuple[str, ...]       # 볼트 기준 문서(프로젝트 폴더 기준)


GATES: dict[str, Gate] = {g.step: g for g in (
    Gate("설계", "가설을 진입·청산·크기·위험 규칙과 파라미터 범위로 문서에 고정한다. 결과를 보기 전에 규칙을 정해 두어야 뒤 관문의 판정이 의미를 가진다.",
         ("볼트 설계 문서에 진입·청산·크기·위험 규칙과 파라미터 범위가 적혀 있다(전략 카드 표시 기준)",
          "다중 비교 시행 수(N)를 시도 이력표에 누적한다 — 리셋하지 않는다"),
         ("AgentTrading Design Spec.md", "design/phase3-next-hypotheses.md", "memory/progress/trial-ledger.md")),
    Gate("근거 데이터 검증", "전략의 근거가 된 수치가 원본에서 확인된 데이터인지 본다. 검증 전 데이터로 만든 전략은 만들지 않는다(2026-10-09 원칙, v2 폐기 사유).",
         ("근거 수치가 원본 검증을 마쳤다(원칙: 검증 전 데이터로 전략 금지)",
          "근거 미달 전략은 다음 후보(next)·운용(active)이 될 수 없다"),
         ("memory/progress/changelog.md", "design/agent-strategy-page.md")),
    Gate("구현", "설계 문서의 규칙을 백테스트할 수 있는 코드로 옮긴다.",
         ("수수료 taker 0.04% / maker 0.02%, 슬리피지 2bp 를 기본 가정으로 체결 비용에 넣는다",),
         ("AgentTrading Design Spec.md", "design/phase3-backtest.md")),
    Gate("워크포워드", "기본 표본 [2018-03-01, 2022-01-01) 안에서 앵커드 워크포워드(첫 학습 12개월, 검증 6개월씩 전진)로 고른 파라미터의 검증 구간 성과를 본다.",
         ("거래 수 ≥ 100 (표본이 작으면 결론을 내지 않는다 — insufficient)",
          "Sharpe ≥ 1.0", "최대 MDD ≤ 30%",
          "DSR ≥ 0.95 — 다중 비교 보정(Deflated Sharpe), 3기준을 완화하지 않고 더한다",
          "게이트 미달 → Phase 4 금지"),
         ("AgentTrading Design Spec.md", "design/phase3-backtest.md")),
    Gate("OOS 1회", "워크포워드를 통과한 선택 1개만 OOS [2022-01-01, 2025-01-01) 에서 한 번 판정한다. 데이터 스누핑을 막기 위해 코드 가드가 있다.",
         ("OOS 판정 = 선택 run 의 OOS net 거래에 3기준(거래 ≥100 · Sharpe ≥1.0 · MDD ≤30%). DSR 은 참고값",
          "유일한 OOS 경로는 `--oos-final <selection.json>` — 다른 선택(sha256)으로 두 번째 OOS 는 거부",
          "Phase 4 진입 자격 = 워크포워드 pass 그리고 OOS pass. 자격이 있어도 착수는 사람이 판단"),
         ("design/phase3-backtest.md",)),
    Gate("페이퍼 8주", "Bybit demo 에서 OOS 를 통과한 변형 1개를 1분봉 마감 시 평가한다. 아래를 전부 충족해야 실거래 검토를 사람에게 올린다.",
         ("운용 기간 최소 8주, 거래 수 ≥ 100 (못 채우면 기간 연장)",
          "같은 기간 Bybit 실제 체결로 재-백테스트한 순수익 대비 페이퍼 순수익 차이 ±2σ 이내",
          "시장가 진입 실측 슬리피지 중앙값 ≤ 2bp, 주문 실패·누락 0건",
          "최대 낙폭 ≤ 30%, 원칙 위반(레버리지 >10배, 손절 미이행) 0건",
          "낙폭 30% 초과 또는 원칙 위반 1건이면 즉시 중단·원인 분석"),
         ("AgentTrading Design Spec.md",)),
    Gate("실거래", "앞 관문을 모두 통과한 전략만 사람 승인 뒤 실거래로 전환한다. 기준을 충족해도 전환은 사람 판단이다.",
         ("워크포워드 → OOS 1회 → 페이퍼 8주 합격 → 사람 승인",
          "현재 live 모드는 코드에서 거부한다(게이트 기록 잠금은 계획 — platform-architecture)",
          "수동 주문 없음 — 화면에는 정지(킬스위치)만 있다"),
         ("AgentTrading Design Spec.md", "design/live-trading-requirements.md", "design/platform-architecture.md")),
)}


def gate_by_key(key: str | None) -> str:
    """URL 키 → 관문 이름. 모르는 키·없음이면 첫 관문."""
    return next((step for step, k in STEP_KEYS.items() if k == key), STEPS[0])


def gate_status_html(step: str, strategies, highlight: str | None = None) -> str:
    """한 관문에서 전략별 상태·사유 표. `highlight` 전략 행을 강조한다."""
    rows = []
    for s in strategies:
        text, bg, fg = STATE_STYLE[s.steps.get(step, "na")]
        cls = "pipe-row gate-row-hit" if s.id == highlight else "pipe-row"
        rows.append(f'<tr class="{cls}"><td class="name"><b>{html.escape(s.id)}</b> {status_badge(s)}</td>'
                    f'<td style="background:{bg};color:{fg}">{text}</td>'
                    f'<td class="note">{html.escape(s.notes.get(step, ""))}</td></tr>')
    return ('<table class="pipe"><tr><th class="name">전략</th><th style="width:12%">상태</th>'
            f'<th class="note">사유</th></tr>{"".join(rows)}</table>')
