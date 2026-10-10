"""페이지 공용: 경로 설정·mtime 키 캐시 로더·카드/KPI/안내 렌더링(Streamlit 의존)."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from src.backtest.export_equity import export_paths
from src.ui import data as ud
from src.ui import theme

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {
    "AT_OUT_DIR": ROOT / "data" / "out" / "backtest",
    "AT_DATA_DIR": ROOT / "data" / "raw" / "normalized" / "bitmex",
    "AT_VAULT_DIR": Path.home() / "Documents" / "Obsidian Vault" / "Projects" / "work" / "AgentTrading",
}
EXPORT_CMD = "python -m src.backtest.export_equity --report data/out/backtest/walkforward/{name}.json"


def path(key: str) -> Path:
    """환경변수(테스트·다른 위치용)가 있으면 그 경로, 없으면 기본 경로."""
    v = os.environ.get(key)
    return Path(v) if v else DEFAULTS[key]


def symbol() -> str:
    return os.environ.get("AT_SYMBOL", "XBTUSD")


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return -1.0


# 캐시: 키에 파일 mtime 을 넣어 산출물이 갱신되면 다시 읽는다 ------------------------------------------------

@st.cache_data(show_spinner=False)
def _report(out: str, name: str, mtime: float):
    return ud.load_report(Path(out), name)


@st.cache_data(show_spinner=False)
def _equity(out: str, name: str, mtime: float):
    return ud.load_equity(Path(out), name)


@st.cache_data(show_spinner=False)
def _wf_trades(out: str, name: str, mtime: float):
    return ud.load_wf_trades(Path(out), name)


@st.cache_data(show_spinner=False)
def _summary(p: str, mtime: float):
    return ud.load_summary(Path(p))


@st.cache_data(show_spinner=False, max_entries=16)
def _roundtrips(out: str, profile: str, span: str, sid: str, pid: str, mtime: float):
    return ud.load_roundtrips(Path(out), profile, span, sid, pid)


@st.cache_data(show_spinner=False, max_entries=32)
def _trade_bars(data_dir: str, sym: str, entry, exit_, pad: float):
    return ud.load_trade_bars(Path(data_dir), sym, entry, exit_, pad)


@st.cache_data(show_spinner=False, max_entries=4)
def _diagnose(p: str, mtime: float):
    return pd.read_parquet(p)


@st.cache_data(show_spinner=False, ttl=60)
def _vault_tasks(vault: str):
    return ud.vault_tasks(Path(vault))


@st.cache_data(show_spinner=False, ttl=60)
def _coverage(data_dir: str, sym: str):
    return ud.coverage(Path(data_dir), sym)


def report(name: str) -> dict | None:
    out = path("AT_OUT_DIR")
    return _report(str(out), name, _mtime(ud.report_path(out, name)))


def equity(name: str) -> pd.DataFrame | None:
    out = path("AT_OUT_DIR")
    return _equity(str(out), name, _mtime(export_paths(ud.report_path(out, name))["equity"]))


def wf_trades(name: str) -> pd.DataFrame | None:
    out = path("AT_OUT_DIR")
    return _wf_trades(str(out), name, _mtime(export_paths(ud.report_path(out, name))["trades"]))


def summary(p: Path):
    return _summary(str(p), _mtime(Path(p)))


def roundtrips(profile: str, span: str, sid: str, pid: str) -> pd.DataFrame:
    out = path("AT_OUT_DIR")
    f = out / "roundtrips_net" / profile / sid / f"{span}.parquet"
    return _roundtrips(str(out), profile, span, sid, pid, _mtime(f))


def trade_bars(entry, exit_, pad: float) -> pd.DataFrame:
    return _trade_bars(str(path("AT_DATA_DIR")), symbol(), entry, exit_, pad)


def diagnose(rep: dict) -> pd.DataFrame | None:
    """리포트와 같은 펀딩 조건의 진단 프레임. 없으면 None."""
    p = ud.diagnose_file(path("AT_OUT_DIR"), rep)
    return _diagnose(str(p), _mtime(p)) if p.is_file() else None


def vault_tasks() -> pd.DataFrame:
    return _vault_tasks(str(path("AT_VAULT_DIR")))


def coverage() -> dict:
    return _coverage(str(path("AT_DATA_DIR")), symbol())


# 렌더링 -----------------------------------------------------------------------------------------------

def setup(title: str) -> None:
    st.markdown(theme.CSS, unsafe_allow_html=True)
    st.title(title)


def card(key: str):
    """흰 배경·그림자 카드 컨테이너(CSS `st-key-card-*`)."""
    return st.container(key=f"card-{key}")


def select_report() -> str | None:
    """사이드바 판정 리포트 선택. 리포트가 없으면 None."""
    names = ud.list_reports(path("AT_OUT_DIR"))
    if not names:
        return None
    default = "default+funding" if "default+funding" in names else names[0]
    return st.sidebar.selectbox("판정 리포트", names, index=names.index(default), key="report")


def default_index(options: list[str], preferred=("default+funding", "default")) -> int:
    """선택지 기본값: 판정 프로필(`default+funding` → `default`) 우선, 없으면 첫 번째."""
    return next((options.index(p) for p in preferred if p in options), 0)


def no_report_notice() -> None:
    st.info("워크포워드 판정 리포트가 없다. 먼저 판정을 실행한다: "
            "`python -m src.backtest.run --walkforward --funding --jobs 4`")


def no_equity_notice(name: str) -> None:
    st.info(f"검증 자본곡선 파일이 없다. 다음 명령으로 만든다: `{EXPORT_CMD.format(name=name)}`")


def stale_notice(name: str) -> None:
    st.warning(f"검증 자본곡선·거래 파일이 판정 리포트보다 오래됐다. 다시 만든다: `{EXPORT_CMD.format(name=name)}`")


def fmt_value(key: str, v: float) -> str:
    if v != v:  # NaN
        return "–"
    if key == "n_trades":
        return f"{int(v):,}"
    if key == "mdd":
        return f"{v:.1%}"
    return f"{v:.4f}" if key == "dsr" else f"{v:.2f}"


def kpi_row(kpis: list[dict]) -> None:
    for col, k, color in zip(st.columns(len(kpis)), kpis, theme.KPI_COLORS):
        th = f"기준 {k['op'].replace('>=', '≥').replace('<=', '≤')} {fmt_value(k['key'], float(k['threshold']))}"
        col.markdown(theme.kpi_card_html(k["label"], fmt_value(k["key"], k["value"]), th, k["ok"], k["key"], color),
                     unsafe_allow_html=True)


def verdict_line(rep: dict) -> None:
    s = ud.phase4_status(rep)
    st.markdown(
        f"{theme.badge_html('판정 ' + theme.verdict_label(rep.get('verdict')), theme.verdict_color(rep.get('verdict')))}"
        f" {theme.badge_html('bybit ' + theme.verdict_label(rep.get('bybit_verdict')), theme.verdict_color(rep.get('bybit_verdict')))}"
        f" <span class='status-line'>{s['text']} · 표본 {rep['sample'][0]} ~ {rep['sample'][1]} · "
        f"run {rep['n_runs']:,} · DSR 시행 수 N={rep['n_trials']}</span>",
        unsafe_allow_html=True)


TRADE_COLUMNS = {
    "entry_ts": "진입", "exit_ts": "청산", "side": "방향", "entry_price": "진입가", "exit_price": "청산가",
    "exit_reason": "청산 사유", "holding_min": "보유(분)", "leverage": "레버리지", "net_ret": "net",
    "n_funding": "펀딩 횟수", "funding_xbt": "펀딩(XBT)", "fold": "폴드",
}


def trade_table(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in TRADE_COLUMNS if c in df.columns]
    return df[cols].rename(columns=TRADE_COLUMNS)


PCT = st.column_config.NumberColumn(format="percent")
TS = st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")
TRADE_CONFIG = {"net": PCT, "진입": TS, "청산": TS}


def chart(fig, key: str) -> None:
    """Plotly 차트 — Streamlit 테마 대신 `charts` 레이아웃(흰 카드 배경·토큰 색)을 그대로 쓴다."""
    st.plotly_chart(fig, theme=None, width="stretch", key=key)
