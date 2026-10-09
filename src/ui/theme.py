"""디자인 토큰 — vue-element-admin(`src/styles/variables.scss`·`views/dashboard/admin`) 값을 옮긴 상수와 CSS.

코드는 복사하지 않고 값만 쓴다. 근거: Obsidian `design/user-dashboard.md` "디자인 레퍼런스".
`.streamlit/config.toml` 테마 값과 같아야 한다(`tests/test_ui_theme.py` 가 검사).
"""

from __future__ import annotations

import html

# 사이드바
SIDEBAR_WIDTH = 210
SIDEBAR_BG = "#304156"
SIDEBAR_TEXT = "#bfcbd9"
SIDEBAR_ACTIVE = "#409EFF"
SIDEBAR_HOVER = "#263445"

# 본문·카드
MAIN_BG = "#f0f2f5"
MAIN_PADDING = 32
CARD_BG = "#ffffff"
CARD_SHADOW = "4px 4px 40px rgba(0,0,0,.05)"
CARD_PADDING = 16
CARD_GAP = 32
TEXT = "#303133"
TEXT_SECONDARY = "#909399"

# KPI 패널(PanelGroup)
KPI_HEIGHT = 108
KPI_ICON_SIZE = 48
KPI_COLORS = ("#40c9c6", "#36a3f7", "#f4516c", "#34bfa3")

# Element UI 상태 색
PRIMARY = "#409EFF"
SUCCESS = "#67C23A"
WARNING = "#E6A23C"
DANGER = "#F56C6C"
INFO = "#909399"

# 라인차트(레퍼런스 expected/actual → default/bybit)
LINE_COLORS = {"default": "#3888fa", "bybit": "#FF005A"}

# 판정 → 상태 색: 통과 success · 미달 danger · 거래 부족 warning · 없음/대기 info
VERDICT_COLORS = {"pass": SUCCESS, "fail": DANGER, "insufficient": WARNING}
VERDICT_LABELS = {"pass": "통과", "fail": "미달", "insufficient": "거래 부족"}


def verdict_color(verdict: str | None) -> str:
    return VERDICT_COLORS.get(verdict or "", INFO)


def verdict_label(verdict: str | None) -> str:
    return VERDICT_LABELS.get(verdict or "", "대기")


CSS = f"""
<style>
[data-testid="stAppViewContainer"], [data-testid="stMain"] {{ background: {MAIN_BG}; }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stMainBlockContainer"] {{ padding: {MAIN_PADDING}px; max-width: 100%; }}
section[data-testid="stSidebar"] {{ width: {SIDEBAR_WIDTH}px !important; min-width: {SIDEBAR_WIDTH}px !important; }}
section[data-testid="stSidebar"] > div {{ background: {SIDEBAR_BG}; }}
[data-testid="stSidebarNav"] a span {{ color: {SIDEBAR_TEXT}; }}
[data-testid="stSidebarNav"] a:hover {{ background: {SIDEBAR_HOVER}; }}
[data-testid="stSidebarNav"] a[aria-current="page"] {{ background: {SIDEBAR_HOVER}; }}
[data-testid="stSidebarNav"] a[aria-current="page"] span {{ color: {SIDEBAR_ACTIVE}; }}
section[data-testid="stSidebar"] [data-testid="stSelectbox"] input {{ color: {TEXT} !important; -webkit-text-fill-color: {TEXT} !important; }}
[class*="st-key-card-"] {{
  background: {CARD_BG}; box-shadow: {CARD_SHADOW}; padding: {CARD_PADDING}px; border-radius: 4px;
}}
.kpi-card {{
  display: flex; align-items: center; justify-content: space-between; height: {KPI_HEIGHT}px;
  background: {CARD_BG}; box-shadow: {CARD_SHADOW}; padding: 0 20px; border-radius: 4px; margin-bottom: 8px;
}}
.kpi-icon {{ width: {KPI_ICON_SIZE}px; height: {KPI_ICON_SIZE}px; }}
.kpi-body {{ text-align: right; }}
.kpi-label {{ color: rgba(0,0,0,.45); font-size: 15px; font-weight: 600; margin-bottom: 6px; }}
.kpi-value {{ font-size: 22px; font-weight: 700; color: {TEXT}; }}
.kpi-th {{ font-size: 12px; color: {TEXT_SECONDARY}; margin-top: 2px; }}
.kpi-bad .kpi-value {{ color: {DANGER}; }}
.badge {{ display: inline-block; padding: 2px 10px; border-radius: 4px; color: #fff; font-size: 13px; font-weight: 600; }}
.status-line {{ color: {TEXT_SECONDARY}; font-size: 13px; }}
</style>
"""

# 48px 아이콘(단색 선). 레퍼런스의 svg-icon 자리에 둔다.
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


def kpi_card_html(label: str, value: str, threshold: str, ok: bool, icon: str, color: str) -> str:
    cls = "kpi-card" if ok else "kpi-card kpi-bad"
    return (f'<div class="{cls}">{icon_svg(icon, color)}<div class="kpi-body">'
            f'<div class="kpi-label">{html.escape(label)}</div><div class="kpi-value">{html.escape(value)}</div>'
            f'<div class="kpi-th">{html.escape(threshold)}</div></div></div>')


def badge_html(text: str, color: str) -> str:
    return f'<span class="badge" style="background:{color}">{html.escape(text)}</span>'
