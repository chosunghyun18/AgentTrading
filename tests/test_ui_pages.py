"""ui 페이지 AppTest — 소형 픽스처 산출물로 예외 0·핵심 요소 존재, 산출물 없을 때 안내 문구."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests import ui_fixtures as fx

PAGES = Path(__file__).resolve().parents[1] / "src" / "ui" / "views"
TIMEOUT = 60


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    return fx.build(tmp_path_factory.mktemp("ui"))


@pytest.fixture
def env(tree, monkeypatch):
    monkeypatch.setenv("AT_OUT_DIR", str(tree["out"]))
    monkeypatch.setenv("AT_DATA_DIR", str(tree["norm"]))
    monkeypatch.setenv("AT_VAULT_DIR", str(tree["vault"]))
    return tree


@pytest.fixture
def empty_env(tmp_path, monkeypatch):
    for k in ("AT_OUT_DIR", "AT_DATA_DIR", "AT_VAULT_DIR"):
        monkeypatch.setenv(k, str(tmp_path / k))


def _run(page: str) -> AppTest:
    at = AppTest.from_file(str(PAGES / page), default_timeout=TIMEOUT).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _md(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def _info(at: AppTest) -> str:
    return "\n".join(i.value for i in at.info)


def test_overview(env):
    at = _run("overview.py")
    md = _md(at)
    assert md.count('class="kpi-card') == 4
    assert "판정 " in md and "Phase 4" in md
    assert len(at.dataframe) == 1  # 최근 검증 거래
    assert [c.label for c in at.checkbox] == ["T-4 · 사람: 원본 수동 다운로드"]
    assert all(c.disabled for c in at.checkbox)


def test_walkforward(env):
    at = _run("walkforward.py")
    assert len(at.dataframe) == 2  # 폴드 표 + DSR 표
    assert len(at.dataframe[0].value) == 2  # 픽스처 폴드 2개
    at.radio(key="wf-profile").set_value("bybit").run()
    assert not at.exception


def test_explore_filters(env):
    at = _run("explore.py")
    assert len(at.dataframe) == 1  # run 표
    at.multiselect(key="ex-trigger").set_value(["h1"]).run()
    assert not at.exception
    assert "표시 2 / 전체 4 run" in _md(at)


def test_trades_walkforward_and_span(env):
    at = _run("trades.py")
    assert len(at.dataframe) == 1  # 거래 표
    assert not at.warning  # 캔들 그림(1분봉 있음)
    at.slider(key="tr-pad").set_value(24).run()
    assert not at.exception
    at.radio(key="tr-source").set_value("백테스트 구간 run").run()
    assert not at.exception
    assert at.selectbox(key="tr-span").value == fx.SPAN
    assert len(at.dataframe) == 1 and len(at.dataframe[0].value) > 0


@pytest.mark.parametrize("page, needle", [("overview.py", "판정 리포트가 없다"), ("walkforward.py", "판정 리포트가 없다"),
                                          ("explore.py", "run 요약이 없다"), ("trades.py", "판정 리포트가 없다")])
def test_empty_outputs_show_notice(empty_env, page, needle):
    at = _run(page)
    assert needle in _info(at)


def test_missing_equity_notice(env, tmp_path, monkeypatch):
    import shutil
    out = tmp_path / "out"
    shutil.copytree(env["out"] / "walkforward", out / "walkforward")
    for p in (out / "walkforward").glob("*.parquet"):
        p.unlink()
    monkeypatch.setenv("AT_OUT_DIR", str(out))
    at = _run("overview.py")
    assert "export_equity" in _info(at)
    assert _md(at).count('class="kpi-card') == 4


def test_trades_span_without_trades(env, tmp_path, monkeypatch):
    import json
    import shutil
    out = tmp_path / "out"
    shutil.copytree(env["out"], out)
    for p in (out / "summary").glob("*/*.json"):
        obj = json.loads(p.read_text(encoding="utf-8"))
        for r in obj["runs"]:
            r["n_trades"] = 0
        p.write_text(json.dumps(obj), encoding="utf-8")
    monkeypatch.setenv("AT_OUT_DIR", str(out))
    at = _run("trades.py")
    at.radio(key="tr-source").set_value("백테스트 구간 run").run()
    assert not at.exception
    assert "거래가 있는 run 이 없다" in _info(at)


def test_trades_row_number_survives_source_switch(env):
    at = _run("trades.py")
    n = len(at.dataframe[0].value)
    at.number_input[0].set_value(n - 1).run()  # 워크포워드 거래의 마지막 행
    assert not at.exception
    at.radio(key="tr-source").set_value("백테스트 구간 run").run()
    assert not at.exception
    assert at.number_input[0].value == 0  # 목록이 바뀌면 새 위젯(0부터)


def test_stale_equity_warning(env, tmp_path, monkeypatch):
    import os
    import shutil
    out = tmp_path / "out"
    shutil.copytree(env["out"], out)
    rep = out / "walkforward" / f"{fx.REPORT}.json"
    t = rep.stat().st_mtime + 100
    os.utime(rep, (t, t))  # 리포트를 자본곡선보다 새것으로
    monkeypatch.setenv("AT_OUT_DIR", str(out))
    at = _run("overview.py")
    assert any("오래됐다" in w.value for w in at.warning)
