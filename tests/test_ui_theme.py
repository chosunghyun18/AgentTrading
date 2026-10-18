"""ui.theme 디자인 토큰 v2 = 벤치마크 값(Obsidian design/ui-ux-v2-benchmark.md), config.toml 테마·서버 설정, ui.sh 로컬 고정."""

import toml
from pathlib import Path

from src.ui import theme

ROOT = Path(__file__).resolve().parents[1]


def test_tokens_match_reference():
    # 중립: Tailwind zinc 950/900/800/100/400/500
    assert (theme.MAIN_BG, theme.SURFACE, theme.BORDER) == ("#09090b", "#18181b", "#27272a")
    assert (theme.TEXT, theme.TEXT_SECONDARY, theme.INFO) == ("#f4f4f5", "#a1a1aa", "#71717a")
    # 손익: FreqUI --color-profit / --color-loss · 경고(LIVE) amber-500 · 기본 blue-500
    assert (theme.PROFIT, theme.LOSS, theme.WARNING, theme.PRIMARY) == ("#12bb7b", "#ef5350", "#f59e0b", "#3b82f6")
    assert theme.SUCCESS == theme.PROFIT and theme.DANGER == theme.LOSS
    assert theme.MODE_COLORS == {"demo": theme.PRIMARY, "live": theme.LOSS}  # LIVE 만 요란(빨강), HALT 는 amber
    assert theme.KPI_COLORS == ("#38bdf8", "#a78bfa", "#f472b6", "#2dd4bf")
    assert (theme.SIDEBAR_WIDTH, theme.SIDEBAR_BG, theme.CARD_BG) == (230, theme.SURFACE, theme.SURFACE)
    assert theme.verdict_color("pass") == theme.PROFIT and theme.verdict_color("fail") == theme.LOSS
    assert theme.verdict_color(None) == theme.INFO


def test_css_uses_tokens():
    for v in (theme.SURFACE, theme.BORDER, theme.PROFIT, theme.LOSS, f"{theme.KPI_HEIGHT}px", f"{theme.SIDEBAR_WIDTH}px",
              "st-key-kill-btn", "st-key-to-live", "tabular-nums"):
        assert v in theme.CSS


def test_config_toml_matches_tokens():
    cfg = toml.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    t, sb = cfg["theme"], cfg["theme"]["sidebar"]
    assert t["base"] == "dark"
    assert (t["primaryColor"], t["backgroundColor"], t["secondaryBackgroundColor"], t["textColor"]) == \
        (theme.PRIMARY, theme.MAIN_BG, theme.SURFACE, theme.TEXT)
    assert (t["borderColor"], t["redColor"], t["greenColor"], t["orangeColor"], t["grayColor"]) == \
        (theme.BORDER, theme.LOSS, theme.PROFIT, theme.WARNING, theme.INFO)
    assert list(t["chartCategoricalColors"]) == list(theme.KPI_COLORS)
    assert (sb["backgroundColor"], sb["textColor"], sb["primaryColor"]) == \
        (theme.SIDEBAR_BG, theme.SIDEBAR_TEXT, theme.SIDEBAR_ACTIVE)
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


def test_no_auto_multipage_dir():
    """`src/ui/pages/` 가 있으면 Streamlit 이 자동 멀티페이지로 잡아 URL 직접 접속 때 app.py(sys.path 설정)를 건너뛴다."""
    assert not (ROOT / "src" / "ui" / "pages").exists()
    assert 'export PYTHONPATH="$PWD' in (ROOT / "scripts" / "ui.sh").read_text(encoding="utf-8")
