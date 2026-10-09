"""전략 페이지 HTML 조각 — 관문 진행표·상태 배지(Streamlit 무관, 단위 테스트 대상)."""

from __future__ import annotations

import html

from src.agent.strategies import STATUSES, STEPS, Strategy
from src.ui import theme

STATE_STYLE = {  # 상태 → (표시, 배경, 글자)
    "done": ("완료", theme.SUCCESS, "#fff"),
    "fail": ("미달", theme.DANGER, "#fff"),
    "running": ("진행 중", theme.PRIMARY, "#fff"),
    "pending": ("대기", "#ecf5ff", theme.PRIMARY),
    "na": ("—", "#f4f4f5", theme.INFO),
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
.agent-now {{ font-size: 26px; font-weight: 700; color: {theme.TEXT}; margin: 4px 0 8px; }}
.rule-h {{ color: {theme.TEXT_SECONDARY}; font-size: 13px; font-weight: 600; margin-top: 8px; }}
</style>
"""


def status_badge(s: Strategy) -> str:
    label, color = STATUSES[s.status]
    return theme.badge_html(label, STATUS_COLORS[color])


def pipeline_html(strategies) -> str:
    """전략 × 관문 표. 셀 title 에 단계별 사유(notes)."""
    head = "".join(f"<th>{html.escape(st)}</th>" for st in STEPS)
    rows = []
    for s in strategies:
        cells = []
        for step in STEPS:
            text, bg, fg = STATE_STYLE[s.steps.get(step, "na")]
            note = html.escape(s.notes.get(step, ""), quote=True)
            cells.append(f'<td style="background:{bg};color:{fg}" title="{note}">{text}</td>')
        rows.append(f'<tr class="pipe-row"><td class="name"><b>{html.escape(s.id)}</b> {status_badge(s)}</td>'
                    + "".join(cells) + "</tr>")
    return f'<table class="pipe"><tr><th class="name">전략</th>{head}</tr>{"".join(rows)}</table>'


def bullets(items) -> str:
    return "\n".join(f"- {x}" for x in items)
