"""화면 v2(벤치마크 적용) — 손익 pill·모드 배지·상태 스냅샷(네트워크 0)·거래 Styler·내비 등록. 설계: Obsidian design/ui-ux-v2-benchmark.md"""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.agent import service, settings
from src.agent.service import TradingService
from src.ui import common, theme
from tests import ui_fixtures as fx
from tests.agent_fakes import FakeClient

ROOT = Path(__file__).resolve().parents[1]


def test_pnl_html_sign_color_and_neutral():
    assert "pill-up" in theme.pnl_html(0.0123) and "▲ +1.23%" in theme.pnl_html(0.0123)
    assert "pill-down" in theme.pnl_html(-0.5, "{:+,.2f} USDT") and "▼ -0.50 USDT" in theme.pnl_html(-0.5, "{:+,.2f} USDT")
    assert "pill-flat" in theme.pnl_html(0.0) and "pill-flat" in theme.pnl_html(float("nan")) and "pill-flat" in theme.pnl_html(None)
    assert "&lt;" in theme.pill_html("<x>", "up")


def test_mode_badge_live_is_loud_and_halt():
    demo, live = theme.mode_badge_html("demo"), theme.mode_badge_html("live", halted=True)
    assert theme.PRIMARY in demo and "DEMO" in demo and "HALT" not in demo
    assert theme.LOSS in live and "LIVE" in live and "HALT" in live and theme.WARNING in live  # LIVE 빨강 · HALT amber


def test_sidebar_and_agent_status_html():
    snap = {"mode": "demo", "halted": False, "has_keys": False, "symbol": "BTCUSDT", "strategy": None, "next": "C1 <x>",
            "report": "default", "verdict": "fail"}
    sb, ag = theme.sidebar_status_html(snap), theme.agent_status_html(snap)
    assert "미가동" in sb and "C1 &lt;x&gt;" in sb and "없음" in sb and "미달" in sb and theme.LOSS in sb
    assert "다음 후보" in ag and "미가동" in ag and "워크포워드" in ag
    assert "판정 리포트 없음" in theme.sidebar_status_html({**snap, "report": None})


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    return fx.build(tmp_path_factory.mktemp("ui"))


@pytest.fixture
def world(tree, tmp_path, monkeypatch):
    monkeypatch.setenv("AT_OUT_DIR", str(tree["out"]))
    monkeypatch.setenv("AT_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setattr(settings, "load_env", lambda: None)
    c = FakeClient()
    monkeypatch.setattr(service, "FACTORY", lambda mode: TradingService(c, mode, tmp_path / "agent", tmp_path / "r.yaml"))
    return c


def test_status_snapshot_makes_no_exchange_calls(world):
    snap = common.status_snapshot()
    assert snap["mode"] == "demo" and snap["halted"] is False and snap["has_keys"] is True
    assert snap["strategy"] is None and snap["report"] == fx.REPORT and snap["verdict"] in ("pass", "fail", "insufficient")
    assert world.calls == []  # 시세·포지션·잔고 등 REST 호출 없음


def test_trade_styler_keeps_columns_and_values():
    df = pd.DataFrame({"entry_ts": pd.to_datetime(["2020-03-01", "2020-03-02"], utc=True),
                       "exit_ts": pd.to_datetime(["2020-03-01 01:00", "2020-03-02 01:00"], utc=True),
                       "side": ["long", "short"], "entry_price": [1.0, 2.0], "exit_price": [1.1, 2.1],
                       "exit_reason": ["tp", "stop"], "holding_min": [60, 60], "leverage": [2, 2],
                       "net_ret": [0.05, np.nan], "extra": [1, 2]})
    sty = common.trade_styler(df)
    assert list(sty.data.columns) == list(common.trade_table(df).columns) and "extra" not in sty.data.columns
    assert sty.data["net"].tolist()[0] == 0.05 and pd.isna(sty.data["net"].tolist()[1])
    html = sty.to_html()
    assert "+5.00%" in html and "▲ 롱" in html and "▼ 숏" in html and theme.PROFIT in html and theme.LOSS in html
    assert common.pnl_css(-1) == f"color:{theme.LOSS}" and common.pnl_css(0) == f"color:{theme.INFO}" and common.pnl_css("x") == ""
    pnl = common.pnl_styler(pd.DataFrame({"closedPnl": [1.5, -2.0], "other": ["a", "b"]}), ["closedPnl", "missing"]).to_html()
    assert "+1.50" in pnl and "-2.00" in pnl


def test_app_registers_all_views_with_dashboard_default():
    src = (ROOT / "src" / "ui" / "app.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'PAGES / "([a-z_]+\.py)"', src))
    views = {p.name for p in (ROOT / "src" / "ui" / "views").glob("*.py")}
    assert registered == views
    assert re.search(r'"overview\.py", title="대시보드".*default=True', src)
    for group in ("홈", "에이전트", "백테스트", "설정·안전"):
        assert f'"{group}": [' in src
    assert "common.sidebar_status()" in src
