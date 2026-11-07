"""디자인 토큰 v3 — 라이트·다크 두 벌 + 시스템(Streamlit 기본: OS 설정 추종). 설계 Obsidian `design/theme-tokens-v3-light-dark.md`.

- 다크 = v2(오픈소스 트레이딩 UI 벤치마크, `design/ui-ux-v2-benchmark.md`). 중립은 Tailwind zinc, 손익은 FreqUI `--color-profit/--color-loss`.
- 라이트 = zinc + 의미 색 600~700 단계 — 흰 배경·카드 모두 글자 대비 WCAG AA 4.5:1 이상(`tests/test_ui_theme.py`).
- 색은 뜻에만 쓴다: 이익/손실/경고(LIVE)/기본/중립. 손익은 색과 글리프(▲/▼)를 함께 쓴다.
- HTML·CSS 색(`theme.PROFIT` 등)은 CSS 변수 — `.stApp` 의 `light-dark()` 로 브라우저가 모드를 즉시 따른다.
  hex 가 필요한 곳(Plotly·표 Styler)은 `tokens()`(서버가 아는 모드, 모르면 다크).
  `.streamlit/config.toml` 의 `[theme.light]`/`[theme.dark]` 와 같아야 한다.
"""

from __future__ import annotations

import html
import math

_NEUTRAL = {  # zinc — 다크 950·900·800·100·400·500 / 라이트 white·100·200·950·600·500
    "dark": dict(MAIN_BG="#09090b", SURFACE="#18181b", BORDER="#27272a", TEXT="#f4f4f5", TEXT_SECONDARY="#a1a1aa", INFO="#71717a"),
    "light": dict(MAIN_BG="#ffffff", SURFACE="#f4f4f5", BORDER="#e4e4e7", TEXT="#09090b", TEXT_SECONDARY="#52525b", INFO="#71717a"),
}
_SEMANTIC = {  # 이익·손실·경고(HALT)·기본 — 다크 FreqUI·amber-500·blue-500 / 라이트 emerald-700·red-700·amber-700·blue-600
    "dark": dict(PROFIT="#12bb7b", LOSS="#ef5350", WARNING="#f59e0b", PRIMARY="#3b82f6"),
    "light": dict(PROFIT="#047857", LOSS="#b91c1c", WARNING="#b45309", PRIMARY="#2563eb"),
}
_CHART = {  # 범주 색(파이·막대, config chartCategoricalColors) · 선 색(프로필, 손익 색과 겹치지 않게) · Plotly 템플릿
    "dark": dict(KPI_COLORS=("#38bdf8", "#a78bfa", "#f472b6", "#2dd4bf"),
                 LINE_COLORS={"default": "#60a5fa", "bybit": "#a78bfa"}, PLOTLY_TEMPLATE="plotly_dark"),
    "light": dict(KPI_COLORS=("#0284c7", "#7c3aed", "#db2777", "#0d9488"),
                  LINE_COLORS={"default": "#2563eb", "bybit": "#7c3aed"}, PLOTLY_TEMPLATE="plotly_white"),
}
MODES = ("dark", "light")


def _build(m: str) -> dict:
    t = {**_NEUTRAL[m], **_SEMANTIC[m], **_CHART[m]}
    t.update(SUCCESS=t["PROFIT"], DANGER=t["LOSS"],  # 판정·배지 별칭(통과 = 이익 색, 미달 = 손실 색)
             SIDEBAR_BG=t["SURFACE"], SIDEBAR_TEXT=t["TEXT_SECONDARY"], SIDEBAR_ACTIVE=t["PRIMARY"], CARD_BG=t["SURFACE"],
             # 모드 — LIVE 만 요란(실제 자금 = 손실 색 빨강, 독립 검토 2026-10-17 MUST-3), DEMO 는 기본색. HALT 는 amber
             MODE_COLORS={"demo": t["PRIMARY"], "live": t["LOSS"]},
             # 판정 → 상태 색: 통과 success · 미달 danger · 거래 부족 warning · 없음/대기 info
             VERDICT_COLORS={"pass": t["PROFIT"], "fail": t["LOSS"], "insufficient": t["WARNING"]})
    return t


_TOKENS = {m: _build(m) for m in MODES}
_COLOR_NAMES = ("MAIN_BG", "SURFACE", "BORDER", "TEXT", "TEXT_SECONDARY", "INFO", "PROFIT", "LOSS", "WARNING", "PRIMARY")


def mode() -> str:
    """서버가 아는 현재 세션 모드. 첫 로드·전환 직후엔 틀릴 수 있다(streamlit#11920) — hex 가 꼭 필요한 곳(Plotly 선·표 Styler)만 쓴다."""
    try:
        import streamlit as st
        return "light" if st.context.theme.type == "light" else "dark"
    except Exception:  # Streamlit 밖
        return "dark"


def tokens(m: str | None = None) -> dict:
    """모드별 hex 토큰. 생략하면 `mode()`."""
    return _TOKENS[m or mode()]


def _var(name: str) -> str:
    return f"var(--at-{name.lower().replace('_', '-')})"


# HTML·CSS 용 색 = CSS 변수. 값은 `CSS` 가 `.stApp` 에 `light-dark(라이트, 다크)` 로 정의하므로 브라우저가 Streamlit 이
# 고른 모드(`.stApp` color-scheme)를 즉시 따른다 — 메뉴 전환에 서버 재실행이 필요 없다.
MAIN_BG, SURFACE, BORDER, TEXT, TEXT_SECONDARY, INFO, PROFIT, LOSS, WARNING, PRIMARY = map(_var, _COLOR_NAMES)
SUCCESS, DANGER = PROFIT, LOSS  # 판정·배지 별칭(통과 = 이익 색, 미달 = 손실 색)
KPI_COLORS = tuple(f"var(--at-cat-{i})" for i in range(4))  # 범주 색(KPI 아이콘) = config chartCategoricalColors
# 모드 — LIVE 만 요란(실제 자금 = 손실 색 빨강, 독립 검토 2026-10-17 MUST-3), DEMO 는 기본색. HALT 는 amber
MODE_COLORS = {"demo": PRIMARY, "live": LOSS}
# 판정 → 상태 색: 통과 success · 미달 danger · 거래 부족 warning · 없음/대기 info
VERDICT_COLORS = {"pass": SUCCESS, "fail": DANGER, "insufficient": WARNING}

# 사이드바·본문·카드
SIDEBAR_WIDTH = 230
SIDEBAR_BG = SURFACE
SIDEBAR_TEXT = TEXT_SECONDARY
SIDEBAR_ACTIVE = PRIMARY
MAIN_PADDING = "24px 32px 48px"
CARD_BG = SURFACE
CARD_PADDING = 16
CARD_RADIUS = 8

# KPI 카드
KPI_HEIGHT = 108
KPI_ICON_SIZE = 40

VERDICT_LABELS = {"pass": "통과", "fail": "미달", "insufficient": "거래 부족"}


def verdict_color(verdict: str | None) -> str:
    return VERDICT_COLORS.get(verdict or "", INFO)


def verdict_label(verdict: str | None) -> str:
    return VERDICT_LABELS.get(verdict or "", "대기")


def _root_vars() -> str:
    lt, dk = _TOKENS["light"], _TOKENS["dark"]
    pairs = [(_var(n)[4:-1], lt[n], dk[n]) for n in _COLOR_NAMES]
    pairs += [(f"--at-cat-{i}", lt["KPI_COLORS"][i], dk["KPI_COLORS"][i]) for i in range(4)]
    return " ".join(f"{k}: light-dark({a}, {b});" for k, a, b in pairs)


CSS = f"""
<style>
.stApp {{ {_root_vars()} }}
[data-testid="stMainBlockContainer"] {{ padding: {MAIN_PADDING}; max-width: 100%; }}
section[data-testid="stSidebar"] {{ width: {SIDEBAR_WIDTH}px !important; min-width: {SIDEBAR_WIDTH}px !important; }}
[class*="st-key-card-"] {{
  background: {CARD_BG}; border: 1px solid {BORDER}; border-radius: {CARD_RADIUS}px; padding: {CARD_PADDING}px;
}}
.kpi-card {{
  display: flex; align-items: center; justify-content: space-between; height: {KPI_HEIGHT}px;
  background: {CARD_BG}; border: 1px solid {BORDER}; border-radius: {CARD_RADIUS}px; padding: 0 18px; margin-bottom: 8px;
}}
.kpi-icon {{ width: {KPI_ICON_SIZE}px; height: {KPI_ICON_SIZE}px; flex: none; }}
.kpi-body {{ text-align: right; }}
.kpi-label {{ color: {TEXT_SECONDARY}; font-size: 13px; font-weight: 600; margin-bottom: 4px; }}
.kpi-value {{ font-size: 24px; font-weight: 700; color: {TEXT}; font-variant-numeric: tabular-nums; }}
.kpi-bad .kpi-value {{ color: {LOSS}; }}
.kpi-ok .kpi-value {{ color: {PROFIT}; }}
.badge {{ display: inline-block; padding: 2px 10px; border-radius: 4px; color: #fff; font-size: 12px; font-weight: 700;
          letter-spacing: .02em; }}
.pill {{ display: inline-block; padding: 1px 8px; border-radius: 999px; border: 1px solid; font-size: 12px; font-weight: 600;
         font-variant-numeric: tabular-nums; white-space: nowrap; }}
.pill-up {{ color: {PROFIT}; border-color: {PROFIT}; }}
.pill-down {{ color: {LOSS}; border-color: {LOSS}; }}
.pill-flat {{ color: {INFO}; border-color: {INFO}; }}
.status-line {{ color: {TEXT_SECONDARY}; font-size: 13px; }}
.sb-status {{ font-size: 13px; color: {TEXT_SECONDARY}; line-height: 2; }}
.sb-status b {{ color: {TEXT}; font-weight: 600; }}
.agent-status {{ display: flex; flex-wrap: wrap; gap: 8px 20px; align-items: center; font-size: 14px; color: {TEXT}; }}
.agent-status .k {{ color: {TEXT_SECONDARY}; font-size: 12px; margin-right: 4px; }}
.terminal-brand {{ display:flex; align-items:center; gap:10px; color:{TEXT}; font-size:18px; font-weight:750; padding:8px 0 18px; }}
.terminal-brand .mark {{ color:{PRIMARY}; font-family:monospace; font-size:25px; }}
.research-context {{ display:flex; flex-wrap:wrap; gap:8px 24px; color:{TEXT_SECONDARY}; font-size:12px; padding:12px 0 18px; }}
.research-context b {{ color:{TEXT}; font-weight:500; font-variant-numeric:tabular-nums; }}
.next-step {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 16px; padding:16px 20px;
              margin:8px 0 20px; border:1px solid {BORDER}; border-left:3px solid {PRIMARY};
              border-radius:{CARD_RADIUS}px; background:{SURFACE}; color:{TEXT_SECONDARY}; line-height:1.5; }}
.next-step .eyebrow {{ color:{PRIMARY}; font-size:12px; font-weight:700; letter-spacing:.04em; white-space:nowrap; }}
.next-step strong {{ color:{TEXT}; font-size:16px; white-space:nowrap; }}
.next-step span:last-child {{ flex:1 1 360px; font-size:13px; }}
[data-testid="stMetricValue"] {{ font-variant-numeric:tabular-nums; }}
[data-testid="stMainBlockContainer"] h1 {{ font-size:1.8rem; letter-spacing:-.035em; }}
@media (max-width: 800px) {{
  [data-testid="stMainBlockContainer"] {{ padding:20px 16px 32px; }}
  .kpi-card {{ padding:0 12px; }}
  .kpi-value {{ font-size:21px; }}
  .next-step {{ padding:14px 16px; }}
}}
.st-key-kill-btn button, .st-key-to-live button {{ background: {LOSS} !important; border-color: {LOSS} !important; color: #fff !important; }}
.st-key-kill-btn button:disabled, .st-key-to-live button:disabled {{ opacity: .4; }}
</style>
"""

# 40px 아이콘(단색 선).
_ICON_PATHS = {
    "n_trades": "M8 16h28l-6-6M40 32H12l6 6",
    "sharpe": "M6 38l12-14 9 8 15-20M34 12h8v8",
    "mdd": "M6 10l12 14 9-8 15 20M34 36h8v-8",
    "dsr": "M24 5l15 6v11c0 10-7 17-15 21C16 39 9 32 9 22V11z M17 24l5 5 9-10",
}


def icon_svg(key: str, color: str) -> str:
    path = _ICON_PATHS.get(key, _ICON_PATHS["sharpe"])
    return (f'<svg class="kpi-icon" viewBox="0 0 48 48" fill="none" stroke="{color}" stroke-width="3" '
            f'stroke-linecap="round" stroke-linejoin="round"><path d="{path}"/></svg>')


def badge_html(text: str, color: str) -> str:
    return f'<span class="badge" style="background:{color}">{html.escape(text)}</span>'


def pill_html(text: str, kind: str) -> str:
    """테두리 pill — kind 는 up·down·flat(FreqUI ProfitPill 관례)."""
    return f'<span class="pill pill-{kind}">{html.escape(text)}</span>'


def pnl_html(v: float | None, fmt: str = "{:+.2%}") -> str:
    """손익 pill: 글리프(▲/▼/–) + 부호 있는 숫자. 0·NaN·None 은 무채색."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return pill_html("–", "flat")
    if v > 0:
        return pill_html("▲ " + fmt.format(v), "up")
    if v < 0:
        return pill_html("▼ " + fmt.format(v), "down")
    return pill_html(fmt.format(v), "flat")


def kpi_card_html(label: str, value: str, threshold: str, ok: bool, icon: str, color: str) -> str:
    """큰 값 아래 작은 글씨는 두지 않는다(2026-11-07 사용자 지시) — 기준은 마우스를 올리면 보인다."""
    cls = "kpi-card kpi-ok" if ok else "kpi-card kpi-bad"
    pill = pill_html("통과", "up") if ok else pill_html("미달", "down")
    return (f'<div class="{cls}" title="{html.escape(threshold)}">{icon_svg(icon, color)}<div class="kpi-body">'
            f'<div class="kpi-label">{html.escape(label)} {pill}</div><div class="kpi-value">{html.escape(value)}</div>'
            f'</div></div>')


def mode_badge_html(mode: str, halted: bool = False) -> str:
    """모드 배지 — DEMO 는 기본색, LIVE 는 빨강(요란). 정지 중이면 amber HALT 배지를 덧붙인다."""
    out = badge_html(mode.upper(), MODE_COLORS.get(mode, INFO))
    if halted:
        out += " " + badge_html("HALT", WARNING)
    return out


def sidebar_status_html(snap: dict) -> str:
    """사이드바 상시 상태(모드·정지·키·전략·판정). `common.status_snapshot()` 결과를 받는다."""
    strategy = html.escape(snap.get("strategy") or "미가동")
    nxt = snap.get("next")
    keys = "있음" if snap.get("has_keys") else "없음"
    verdict = snap.get("verdict")
    lines = [
        mode_badge_html(snap.get("mode", "demo"), bool(snap.get("halted"))),
        f"전략 <b>{strategy}</b>" + (f" · 다음 {html.escape(nxt)}" if nxt else ""),
        f"API 키 <b>{keys}</b> · {html.escape(snap.get('symbol') or '')}",
        "판정 " + badge_html(verdict_label(verdict), verdict_color(verdict)) if snap.get("report") else "판정 리포트 없음",
    ]
    return '<div class="sb-status">' + "<br>".join(lines) + "</div>"


def agent_status_html(snap: dict) -> str:
    """대시보드 맨 위 에이전트 상태 한 줄(모드·HALT·전략·다음 후보·키·판정)."""
    strategy = html.escape(snap.get("strategy") or "없음 — 미가동")
    parts = [mode_badge_html(snap.get("mode", "demo"), bool(snap.get("halted"))),
             f'<span><span class="k">전략</span><b>{strategy}</b></span>']
    if snap.get("next"):
        parts.append(f'<span><span class="k">다음 후보</span>{html.escape(snap["next"])}</span>')
    parts.append(f'<span><span class="k">API 키</span>{"있음" if snap.get("has_keys") else "없음"}</span>')
    if snap.get("report"):
        v = snap.get("verdict")
        parts.append(f'<span><span class="k">워크포워드</span>{badge_html(verdict_label(v), verdict_color(v))}</span>')
    return '<div class="agent-status">' + "".join(parts) + "</div>"
