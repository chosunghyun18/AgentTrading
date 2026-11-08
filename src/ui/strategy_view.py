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
.pipe.terms td {{ background: {theme.MAIN_BG}; }}
.pipe.terms td.note {{ white-space: normal; }}
.gate-plain {{ font-size: 17px; line-height: 1.7; color: {theme.TEXT}; }}
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


# 관문 설명 — 쉬운 말 풀이를 앞에, 볼트 원문 기준을 접힌 영역에 둔다(원문 수치는 바꾸지 않는다). 설계: `task/ui-gate-detail-page.md`

INTRO = ("전략(언제 사고 언제 팔지 정한 규칙)이 진짜 돈으로 자동 거래하기 전에 통과해야 하는 **시험 7개**다. "
         "왼쪽부터 차례로 통과해야 다음 시험을 볼 수 있고, 하나라도 떨어지면 거기서 멈춘다.")

TERMS: dict[str, str] = {  # 용어 → 쉬운 풀이
    "전략": "언제 사고, 언제 팔고, 얼마나 살지를 정해 둔 규칙 묶음.",
    "파라미터": "규칙 안의 조절 숫자. 예: \"가격이 평소보다 2.5배 넘게 움직이면 산다\" 의 2.5.",
    "시행 수(N)": "지금까지 시험해 본 규칙·숫자 조합의 개수. 많이 시험할수록 운 좋게 잘 나온 조합이 섞이기 쉬워서 기록해 둔다.",
    "원본 데이터": "전략의 근거로 쓴 거래 기록·가격을 처음 나온 곳(거래소 원 기록 등)에서 직접 받은 것. 남이 옮겨 적은 숫자는 틀릴 수 있다.",
    "백테스트": "과거 가격에 규칙을 그대로 적용해 \"그때 이렇게 거래했으면 얼마를 벌거나 잃었을까\" 를 계산하는 것.",
    "수수료(테이커·메이커)": "거래소에 내는 수수료. 바로 사고팔면(테이커) 0.04%, 원하는 가격에 주문을 걸어 두고 기다리면(메이커) 0.02%.",
    "슬리피지": "주문한 가격과 실제로 사고판 가격의 차이로 생기는 손해. 2bp = 0.02%.",
    "bp": "0.01%. 2bp 는 0.02%.",
    "워크포워드": "과거 기간을 잘라 \"앞 기간으로 숫자를 고르고 → 바로 뒤의 처음 보는 기간으로 채점\" 하기를 앞으로 밀어 가며 여러 번 반복하는 모의고사.",
    "거래 수": "한 번 사서 판 것(또는 팔고 되산 것)을 1번으로 센다. 너무 적으면 운인지 실력인지 알 수 없다.",
    "Sharpe(샤프 비율)": "번 돈을 잔고가 오르락내리락한 정도로 나눈 점수. 같은 수익이면 덜 출렁일수록 높다. 1.0 이상이면 출렁임에 비해 수익이 충분하다고 본다.",
    "MDD(최대 낙폭)": "잔고가 가장 높았을 때에서 가장 많이 떨어진 비율. 30% 는 100만 원이 70만 원까지 줄어든 것.",
    "DSR": "규칙을 많이 시험하면 우연히 좋아 보이는 것이 하나쯤 나온다. 시험한 개수만큼 점수를 깎아서 다시 본 Sharpe. 0.95 이상이면 운만으로 나온 결과가 아닐 가능성이 95% 이상.",
    "Phase 4": "이 프로젝트의 4단계 = 에이전트(프로그램)가 자동으로 거래하는 단계.",
    "OOS(표본 외 기간)": "개발하는 동안 한 번도 보지 않고 아껴 둔 기간(2022~2024년). 처음 보는 시험지라서 실력을 공정하게 잴 수 있다.",
    "데이터 스누핑": "같은 과거 데이터를 계속 들여다보며 규칙을 고치다 보면, 과거에만 딱 맞고 앞으로는 안 맞는 규칙이 되는 것.",
    "페이퍼 트레이딩": "거래소 데모 계좌의 가짜 돈으로, 지금 실제 시장에서 실시간으로 거래해 보는 연습.",
    "2σ(표준편차 2배)": "평소 하루 수익이 흔들리는 폭의 2배. 차이가 이 안이면 \"우연히 생길 수 있는 차이\" 로 본다.",
    "레버리지": "빌린 돈까지 써서 원금보다 크게 거래하는 것. 10배 = 100만 원으로 1,000만 원어치 거래.",
    "손절": "손실이 정해 둔 선에 닿으면 팔아서 더 잃지 않게 끊는 것.",
    "킬스위치": "모든 자동 거래를 즉시 멈추는 비상 정지 버튼.",
}


@dataclass(frozen=True)
class Gate:
    step: str
    alias: str                  # 쉬운 이름
    plain: str                  # 쉽게 말하면
    criteria: tuple[str, ...]   # 통과 조건(쉬운 말)
    exact: tuple[str, ...]      # 정확한 기준(볼트 원문)
    terms: tuple[str, ...]      # 이 관문에 나오는 용어(TERMS 키)
    docs: tuple[str, ...]       # 볼트 기준 문서(프로젝트 폴더 기준)


GATES: dict[str, Gate] = {g.step: g for g in (
    Gate("설계", "규칙을 먼저 글로 정하기",
         "언제 사고, 언제 팔고, 얼마나 살지를 결과를 보기 **전에** 글로 적어 둔다. "
         "결과를 보고 나서 규칙을 고치면, 과거 성적에 끼워 맞춘 규칙이 되기 때문이다.",
         ("사고·팔고·얼마나 살지·손실을 어디서 끊을지와 조절 숫자의 범위가 설계 문서에 적혀 있다",
          "지금까지 시험해 본 규칙 개수를 기록에 계속 더한다 — 뒤의 워크포워드 시험에서 \"운 효과\" 를 깎을 때 쓴다"),
         ("볼트 설계 문서에 진입·청산·크기·위험 규칙과 파라미터 범위가 적혀 있다(전략 카드 표시 기준)",
          "다중 비교 시행 수(N)를 시도 이력표에 누적한다 — 리셋하지 않는다"),
         ("전략", "파라미터", "시행 수(N)", "손절"),
         ("AgentTrading Design Spec.md", "design/phase3-next-hypotheses.md", "memory/progress/trial-ledger.md")),
    Gate("근거 데이터 검증", "근거가 진짜인지 확인하기",
         "전략은 \"이런 패턴이 있다\" 는 근거에서 출발한다. 그 근거 숫자가 **원본 데이터에서 직접 확인된 것**이 아니면 전략을 만들지 않는다. "
         "실제로 v2 전략은 확인 전 숫자로 만들었다가 이 이유로 폐기했다.",
         ("근거로 쓴 숫자가 원본 데이터로 확인됐다",
          "확인되지 않은 전략은 다음 후보나 운용 전략이 될 수 없다"),
         ("근거 수치가 원본 검증을 마쳤다(원칙: 검증 전 데이터로 전략 금지)",
          "근거 미달 전략은 다음 후보(next)·운용(active)이 될 수 없다"),
         ("전략", "원본 데이터"),
         ("memory/progress/changelog.md", "design/agent-strategy-page.md")),
    Gate("구현", "프로그램으로 만들기",
         "글로 적은 규칙을, 컴퓨터가 과거 가격으로 돌려 볼 수 있는 프로그램(백테스트)으로 만든다. "
         "이때 거래 비용을 꼭 넣는다 — 비용을 빼고 계산하면 실제보다 좋아 보인다.",
         ("거래할 때마다 내는 수수료(0.02~0.04%)와, 원하는 가격보다 불리하게 체결되는 손해(0.02%)를 계산에 넣는다",),
         ("수수료 taker 0.04% / maker 0.02%, 슬리피지 2bp 를 기본 가정으로 체결 비용에 넣는다",),
         ("백테스트", "수수료(테이커·메이커)", "슬리피지", "bp"),
         ("AgentTrading Design Spec.md", "design/phase3-backtest.md")),
    Gate("워크포워드", "과거 데이터로 여러 번 모의고사",
         "2018년 3월~2021년 12월 가격을 잘라서, \"앞부분으로 숫자를 고르고 → 바로 뒤 처음 보는 6개월로 채점\" 하기를 앞으로 밀어 가며 반복한다. "
         "공부한 문제가 아니라 **처음 보는 문제로 채점**하는 모의고사라서, 과거에 끼워 맞춘 규칙을 걸러 낸다.",
         ("거래가 100번 이상 — 너무 적으면 운인지 실력인지 몰라 판단을 보류한다",
          "Sharpe 1.0 이상 — 잔고가 출렁인 정도에 비해 수익이 충분하다",
          "최대 낙폭 30% 이하 — 가장 좋았을 때보다 30% 넘게 잃은 적이 없다",
          "DSR 0.95 이상 — 규칙을 여러 개 시험해서 운 좋게 하나 걸린 것이 아닐 가능성이 95% 이상",
          "하나라도 못 넘으면 자동 거래 단계(Phase 4)로 갈 수 없다"),
         ("기본 표본 [2018-03-01, 2022-01-01) 앵커드 워크포워드(첫 학습 12개월, 검증 6개월씩 전진)",
          "거래 수 ≥ 100 (표본이 작으면 결론을 내지 않는다 — insufficient)",
          "Sharpe ≥ 1.0", "최대 MDD ≤ 30%",
          "DSR ≥ 0.95 — 다중 비교 보정(Deflated Sharpe), 3기준을 완화하지 않고 더한다",
          "게이트 미달 → Phase 4 금지"),
         ("워크포워드", "거래 수", "Sharpe(샤프 비율)", "MDD(최대 낙폭)", "DSR", "시행 수(N)", "Phase 4"),
         ("AgentTrading Design Spec.md", "design/phase3-backtest.md")),
    Gate("OOS 1회", "아껴 둔 기간으로 딱 한 번 보는 최종 시험",
         "개발하는 내내 **한 번도 보지 않고 숨겨 둔** 2022~2024년 가격으로, 최종으로 고른 규칙 하나만 **딱 한 번** 시험한다. "
         "수능처럼 재시험이 없다 — 결과를 보고 규칙을 바꿔 다시 보면 시험의 의미가 사라지므로, 프로그램이 두 번째 시험을 막는다.",
         ("워크포워드와 같은 3가지 기준(거래 100번 이상 · Sharpe 1.0 이상 · 최대 낙폭 30% 이하)을 넘는다",
          "다른 규칙으로 이 시험을 다시 보려 하면 프로그램이 거부한다",
          "워크포워드와 이 시험을 둘 다 통과해야 자동 거래 단계 자격이 생긴다. 그래도 시작은 사람이 정한다"),
         ("OOS [2022-01-01, 2025-01-01) 에서 선택 run 의 OOS net 거래에 3기준(거래 ≥100 · Sharpe ≥1.0 · MDD ≤30%). DSR 은 참고값",
          "유일한 OOS 경로는 `--oos-final <selection.json>` — 다른 선택(sha256)으로 두 번째 OOS 는 거부",
          "Phase 4 진입 자격 = 워크포워드 pass 그리고 OOS pass. 자격이 있어도 착수는 사람이 판단"),
         ("OOS(표본 외 기간)", "데이터 스누핑", "Sharpe(샤프 비율)", "MDD(최대 낙폭)", "Phase 4"),
         ("design/phase3-backtest.md",)),
    Gate("페이퍼 8주", "가짜 돈으로 8주 실전 연습",
         "거래소(Bybit) 데모 계좌의 **가짜 돈**으로, 지금 실제 시장에서 실시간으로 8주 이상 굴려 본다. "
         "과거 계산이 아니라 **지금 시장에서도 계산대로 움직이는지** 확인하는 단계다. 아래를 전부 지켜야 진짜 돈 검토로 넘어간다.",
         ("8주 이상 운영하고 거래가 100번 이상 — 모자라면 기간을 늘린다",
          "실제 연습 결과가 같은 기간을 프로그램으로 계산한 결과와 크게 다르지 않다(평소 하루 흔들림의 2배 안)",
          "주문 가격 손해가 보통 0.02% 이하이고, 주문 실패·누락이 0번",
          "최대 낙폭 30% 이하, 그리고 빌린 돈 10배 초과·손절 안 함 같은 원칙 위반이 0번",
          "낙폭이 30%를 넘거나 원칙 위반이 1번이라도 나오면 즉시 멈추고 원인을 찾는다"),
         ("Bybit demo, OOS 를 통과한 변형 1개, 1분봉 마감 시 평가",
          "운용 기간 최소 8주, 거래 수 ≥ 100 (못 채우면 기간 연장)",
          "같은 기간 Bybit 실제 체결로 재-백테스트한 순수익 대비 페이퍼 순수익 차이 ±2σ 이내",
          "시장가 진입 실측 슬리피지 중앙값 ≤ 2bp, 주문 실패·누락 0건",
          "최대 낙폭 ≤ 30%, 원칙 위반(레버리지 >10배, 손절 미이행) 0건",
          "낙폭 30% 초과 또는 원칙 위반 1건이면 즉시 중단·원인 분석"),
         ("페이퍼 트레이딩", "거래 수", "백테스트", "2σ(표준편차 2배)", "슬리피지", "MDD(최대 낙폭)", "레버리지", "손절"),
         ("AgentTrading Design Spec.md",)),
    Gate("실거래", "진짜 돈으로 거래",
         "앞의 시험을 모두 통과하고 **사람이 승인**한 전략만 진짜 돈으로 거래한다. 시험을 다 통과해도 저절로 시작되지 않는다.",
         ("모의고사(워크포워드) → 최종 시험(OOS) → 가짜 돈 8주 연습 합격 → 사람 승인",
          "지금은 프로그램이 진짜 돈 모드를 막아 두었다",
          "화면에서 직접 주문하는 기능은 없다 — 비상 정지 버튼만 있다"),
         ("워크포워드 → OOS 1회 → 페이퍼 8주 합격 → 사람 승인",
          "현재 live 모드는 코드에서 거부한다(게이트 기록 잠금은 계획 — platform-architecture)",
          "수동 주문 없음 — 화면에는 정지(킬스위치)만 있다"),
         ("킬스위치", "페이퍼 트레이딩", "OOS(표본 외 기간)", "워크포워드"),
         ("AgentTrading Design Spec.md", "design/live-trading-requirements.md", "design/platform-architecture.md")),
)}


def terms_html(keys) -> str:
    """용어 풀이 표."""
    rows = "".join(f'<tr class="term-row"><td class="name"><b>{html.escape(k)}</b></td>'
                   f'<td class="note">{html.escape(TERMS[k])}</td></tr>' for k in keys)
    return f'<table class="pipe terms"><tr><th class="name">용어</th><th class="note">뜻</th></tr>{rows}</table>'


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
