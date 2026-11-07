"""ui.theme 디자인 토큰 v3 = 라이트·다크 두 벌(Obsidian design/theme-tokens-v3-light-dark.md), config.toml 모드별 테마·서버 설정, ui.sh 로컬 고정."""

import toml
from pathlib import Path

import pytest

from src.ui import theme

ROOT = Path(__file__).resolve().parents[1]


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_dark_tokens_match_v2_reference():
    t = theme.tokens("dark")
    # 중립: Tailwind zinc 950/900/800/100/400/500
    assert (t["MAIN_BG"], t["SURFACE"], t["BORDER"]) == ("#09090b", "#18181b", "#27272a")
    assert (t["TEXT"], t["TEXT_SECONDARY"], t["INFO"]) == ("#f4f4f5", "#a1a1aa", "#71717a")
    # 손익: FreqUI --color-profit / --color-loss · 경고(HALT) amber-500 · 기본 blue-500
    assert (t["PROFIT"], t["LOSS"], t["WARNING"], t["PRIMARY"]) == ("#12bb7b", "#ef5350", "#f59e0b", "#3b82f6")
    assert t["KPI_COLORS"] == ("#38bdf8", "#a78bfa", "#f472b6", "#2dd4bf")


def test_light_tokens_match_v3_reference():
    t = theme.tokens("light")
    assert (t["MAIN_BG"], t["SURFACE"], t["BORDER"]) == ("#ffffff", "#f4f4f5", "#e4e4e7")
    assert (t["TEXT"], t["TEXT_SECONDARY"], t["INFO"]) == ("#09090b", "#52525b", "#71717a")
    assert (t["PROFIT"], t["LOSS"], t["WARNING"], t["PRIMARY"]) == ("#047857", "#b91c1c", "#b45309", "#2563eb")
    assert t["KPI_COLORS"] == ("#0284c7", "#7c3aed", "#db2777", "#0d9488")


def test_light_text_colors_pass_wcag_aa():
    """라이트에서 글자로 쓰는 색은 흰 배경·카드 모두 4.5:1 이상, 범주·선 색(그래픽)은 3:1 이상."""
    t = theme.tokens("light")
    for bg in (t["MAIN_BG"], t["SURFACE"]):
        for k in ("TEXT", "TEXT_SECONDARY", "PROFIT", "LOSS", "WARNING", "PRIMARY"):
            assert _contrast(t[k], bg) >= 4.5, (k, bg)
        for c in (*t["KPI_COLORS"], *t["LINE_COLORS"].values()):
            assert _contrast(c, bg) >= 3.0, (c, bg)


@pytest.mark.parametrize("m", theme.MODES)
def test_derived_tokens(m):
    t = theme.tokens(m)
    assert t["SUCCESS"] == t["PROFIT"] and t["DANGER"] == t["LOSS"]
    assert t["MODE_COLORS"] == {"demo": t["PRIMARY"], "live": t["LOSS"]}  # LIVE 만 요란(빨강), HALT 는 amber
    assert (t["SIDEBAR_BG"], t["CARD_BG"]) == (t["SURFACE"], t["SURFACE"])
    assert set(t["LINE_COLORS"]) == {"default", "bybit"}


def test_html_colors_are_css_vars_and_server_mode_default_dark():
    """HTML·CSS 색은 CSS 변수(브라우저가 모드 선택), hex 는 tokens() — Streamlit 밖(모드 미상)에서는 다크."""
    assert theme.mode() == "dark" and theme.tokens() is theme.tokens("dark")
    assert (theme.PROFIT, theme.LOSS, theme.SUCCESS, theme.CARD_BG) == ("var(--at-profit)", "var(--at-loss)", "var(--at-profit)", "var(--at-surface)")
    assert theme.MODE_COLORS == {"demo": theme.PRIMARY, "live": theme.LOSS}
    assert theme.KPI_COLORS == tuple(f"var(--at-cat-{i})" for i in range(4))
    assert theme.verdict_color("pass") == theme.PROFIT and theme.verdict_color("fail") == theme.LOSS
    assert theme.verdict_color(None) == theme.INFO


def test_css_defines_light_dark_vars():
    """`.stApp` 에 변수마다 light-dark(라이트, 다크) — Streamlit 이 .stApp color-scheme 을 모드에 맞춰 바꾼다."""
    lt, dk = theme.tokens("light"), theme.tokens("dark")
    for n in ("MAIN_BG", "SURFACE", "BORDER", "TEXT", "TEXT_SECONDARY", "INFO", "PROFIT", "LOSS", "WARNING", "PRIMARY"):
        assert f"--at-{n.lower().replace('_', '-')}: light-dark({lt[n]}, {dk[n]});" in theme.CSS
    for i in range(4):
        assert f"--at-cat-{i}: light-dark({lt['KPI_COLORS'][i]}, {dk['KPI_COLORS'][i]});" in theme.CSS
    for v in (theme.SURFACE, theme.BORDER, theme.PROFIT, theme.LOSS, f"{theme.KPI_HEIGHT}px", f"{theme.SIDEBAR_WIDTH}px",
              "st-key-kill-btn", "st-key-to-live", "tabular-nums"):
        assert v in theme.CSS
    assert "kpi-th" not in theme.CSS and "{{" not in theme.CSS


def test_config_toml_matches_tokens():
    cfg = toml.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert "base" not in cfg["theme"]  # base 가 있으면 한 모드로 고정 — 라이트·다크·시스템 선택이 사라진다
    for m in theme.MODES:
        t, c = theme.tokens(m), cfg["theme"][m]
        sb = c["sidebar"]
        assert (c["primaryColor"], c["backgroundColor"], c["secondaryBackgroundColor"], c["textColor"]) == \
            (t["PRIMARY"], t["MAIN_BG"], t["SURFACE"], t["TEXT"])
        assert (c["borderColor"], c["redColor"], c["greenColor"], c["orangeColor"], c["grayColor"]) == \
            (t["BORDER"], t["LOSS"], t["PROFIT"], t["WARNING"], t["INFO"])
        assert list(c["chartCategoricalColors"]) == list(t["KPI_COLORS"])
        assert (sb["backgroundColor"], sb["textColor"], sb["primaryColor"]) == \
            (t["SIDEBAR_BG"], t["SIDEBAR_TEXT"], t["SIDEBAR_ACTIVE"])
    assert cfg["server"]["address"] == "127.0.0.1" and cfg["server"]["headless"] is True
    assert cfg["browser"]["gatherUsageStats"] is False


def test_ui_sh_local_only():
    s = (ROOT / "scripts" / "ui.sh").read_text(encoding="utf-8")
    assert "--server.address 127.0.0.1" in s and "--server.headless true" in s
    assert "0.0.0.0" not in s


def test_kpi_card_escapes_and_marks_failure():
    h = theme.kpi_card_html("<b>", "1", "기준", False, "sharpe", theme.KPI_COLORS[0])
    assert "&lt;b&gt;" in h and "kpi-bad" in h and "미달" in h
    ok = theme.kpi_card_html("x", "1", "기준", True, "sharpe", theme.KPI_COLORS[0])
    assert "kpi-ok" in ok and "통과" in ok and "kpi-bad" not in ok
    # 큰 값 아래 작은 글씨 없음 — 기준은 툴팁(title)
    assert 'title="기준"' in ok and "kpi-th" not in ok


def test_no_auto_multipage_dir():
    """`src/ui/pages/` 가 있으면 Streamlit 이 자동 멀티페이지로 잡아 URL 직접 접속 때 app.py(sys.path 설정)를 건너뛴다."""
    assert not (ROOT / "src" / "ui" / "pages").exists()
    assert 'export PYTHONPATH="$PWD' in (ROOT / "scripts" / "ui.sh").read_text(encoding="utf-8")
