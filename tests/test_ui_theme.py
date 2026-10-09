"""ui.theme 디자인 토큰 = 레퍼런스(vue-element-admin) 값, config.toml 테마·서버 설정, ui.sh 로컬 고정."""

import toml
from pathlib import Path

from src.ui import theme

ROOT = Path(__file__).resolve().parents[1]


def test_tokens_match_reference():
    assert (theme.SIDEBAR_WIDTH, theme.SIDEBAR_BG, theme.SIDEBAR_TEXT, theme.SIDEBAR_ACTIVE, theme.SIDEBAR_HOVER) == \
        (210, "#304156", "#bfcbd9", "#409EFF", "#263445")
    assert (theme.MAIN_BG, theme.MAIN_PADDING) == ("#f0f2f5", 32)
    assert (theme.CARD_SHADOW, theme.CARD_PADDING, theme.CARD_GAP) == ("4px 4px 40px rgba(0,0,0,.05)", 16, 32)
    assert (theme.KPI_HEIGHT, theme.KPI_ICON_SIZE) == (108, 48)
    assert theme.KPI_COLORS == ("#40c9c6", "#36a3f7", "#f4516c", "#34bfa3")
    assert (theme.PRIMARY, theme.SUCCESS, theme.WARNING, theme.DANGER, theme.INFO) == \
        ("#409EFF", "#67C23A", "#E6A23C", "#F56C6C", "#909399")
    assert theme.verdict_color("pass") == theme.SUCCESS and theme.verdict_color("fail") == theme.DANGER
    assert theme.verdict_color(None) == theme.INFO


def test_css_uses_tokens():
    for v in (theme.SIDEBAR_BG, theme.SIDEBAR_HOVER, theme.SIDEBAR_ACTIVE, theme.MAIN_BG, theme.CARD_SHADOW,
              f"{theme.KPI_HEIGHT}px", f"{theme.SIDEBAR_WIDTH}px"):
        assert v in theme.CSS


def test_config_toml_matches_tokens():
    cfg = toml.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    t, sb = cfg["theme"], cfg["theme"]["sidebar"]
    assert t["base"] == "light"
    assert (t["primaryColor"], t["backgroundColor"]) == (theme.PRIMARY, theme.MAIN_BG)
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
    assert "&lt;b&gt;" in h and "kpi-bad" in h


def test_no_auto_multipage_dir():
    """`src/ui/pages/` 가 있으면 Streamlit 이 자동 멀티페이지로 잡아 URL 직접 접속 때 app.py(sys.path 설정)를 건너뛴다."""
    assert not (ROOT / "src" / "ui" / "pages").exists()
    assert 'export PYTHONPATH="$PWD' in (ROOT / "scripts" / "ui.sh").read_text(encoding="utf-8")
