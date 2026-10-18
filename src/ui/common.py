"""페이지 공용: 경로 설정·mtime 키 캐시 로더·카드/KPI/안내 렌더링(Streamlit 의존)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.agent import strategies as sg
from src.backtest.export_equity import export_paths
from src.shared import settings as cfg
from src.ui import data as ud
from src.ui import theme, trading

ROOT = cfg.ROOT
EXPORT_CMD = "python -m src.backtest.export_equity --report data/out/backtest/walkforward/{name}.json"


def path(key: str) -> Path:
    """`AT_OUT_DIR`·`AT_DATA_DIR` 같은 경로 설정 — 환경변수 → `.env` → 기본값(`src/shared/settings.py`)."""
    p = cfg.path(key)
    if p is None:
        raise KeyError(f"{key} 미설정")
    return p


def vault_dir() -> Path | None:
    """볼트 프로젝트 폴더 — `AT_VAULT_DIR` 를 설정했을 때만(기본값 없음, 선택 기능)."""
    return cfg.path("AT_VAULT_DIR")


def symbol() -> str:
    return cfg.get("AT_SYMBOL")


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
def _vault_tasks(vault: str | None):
    return ud.vault_tasks(Path(vault) if vault else None)


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
    d = vault_dir()
    return _vault_tasks(str(d) if d else None)


def coverage() -> dict:
    return _coverage(str(path("AT_DATA_DIR")), symbol())


# 렌더링 -----------------------------------------------------------------------------------------------

def setup(title: str) -> None:
    st.markdown(theme.CSS, unsafe_allow_html=True)
    st.title(title)


def default_report_name() -> str | None:
    """현재 선택 → `default+funding` → 첫 번째. 삭제된 선택은 무시한다."""
    names = ud.list_reports(path("AT_OUT_DIR"))
    if not names:
        return None
    selected = st.session_state.get("report")
    if selected in names:
        return selected
    return "default+funding" if "default+funding" in names else names[0]


def status_snapshot() -> dict:
    """사이드바·대시보드 상태 한 묶음 — 네트워크 호출 없음(모드·HALT 파일·키 유무·전략 레지스트리·리포트 JSON 만).

    벤치마크 관례(FreqUI Dry/Live 배지·OctoBot 상태 아이콘·LangAlpha "live 만 요란"): 모드·정지·전략·판정이 어느 화면에서나 보인다.
    """
    svc = trading.service()
    reg = sg.registry()
    now = sg.active(reg)
    nxt = next((s for s in reg if s.status == "next"), None)
    name = default_report_name()
    rep = report(name) if name else None
    return {"mode": svc.mode, "halted": bool(svc.halted()), "has_keys": bool(svc.client.has_keys), "symbol": svc.symbol,
            "strategy": now.name if now else None, "next": nxt.name if nxt else None,
            "report": name if rep is not None else None, "verdict": rep.get("verdict") if rep is not None else None}


def sidebar_status() -> None:
    st.sidebar.markdown(theme.sidebar_status_html(status_snapshot()), unsafe_allow_html=True)


def agent_status_card() -> None:
    """대시보드 맨 위 — 에이전트가 지금 무엇으로 거래하나(모드·HALT·적용 전략·다음 후보·키·판정)."""
    with card("agent"):
        st.markdown(theme.agent_status_html(status_snapshot()), unsafe_allow_html=True)


def card(key: str):
    """테마 표면·경계 카드 컨테이너(CSS `st-key-card-*`)."""
    return st.container(key=f"card-{key}")


def select_report() -> str | None:
    """사이드바 판정 리포트 선택. 리포트가 없으면 None."""
    names = ud.list_reports(path("AT_OUT_DIR"))
    if not names:
        return None
    return st.sidebar.selectbox("판정 리포트", names, index=names.index(default_report_name()), key="report")


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
TRADE_CONFIG = {"진입": TS, "청산": TS}  # net 은 `trade_styler` 가 부호·색까지 붙여 표시한다

SIDE_GLYPH = {"long": "▲ 롱", "short": "▼ 숏"}


def pnl_css(v) -> str:
    """손익 셀 CSS — 이익 PROFIT · 손실 LOSS · 0/결측 INFO(색만으로 뜻을 전하지 않도록 값에는 부호를 붙인다)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    if x != x:
        return f"color:{theme.INFO}"
    return f"color:{theme.PROFIT}" if x > 0 else f"color:{theme.LOSS}" if x < 0 else f"color:{theme.INFO}"


def _side_css(v) -> str:
    return f"color:{theme.PROFIT}" if v == "long" else f"color:{theme.LOSS}" if v == "short" else ""


def trade_styler(df: pd.DataFrame):
    """거래 표 Styler — net 은 부호 있는 % 와 손익 색, 방향은 글리프(▲ 롱 / ▼ 숏)와 색. 열 순서·값은 `trade_table` 과 같다."""
    t = trade_table(df)
    sty = t.style
    if "net" in t.columns:
        sty = sty.map(pnl_css, subset=["net"]).format("{:+.2%}", subset=["net"], na_rep="–")
    if "방향" in t.columns:
        sty = sty.map(_side_css, subset=["방향"]).format(lambda v: SIDE_GLYPH.get(v, v), subset=["방향"])
    return sty


def pnl_styler(df: pd.DataFrame, cols, fmt: str = "{:+,.2f}"):
    """임의 표의 손익 열(`cols`)에 부호·색을 붙인 Styler. 없는 열은 건너뛴다."""
    sty = df.style
    for c in (c for c in cols if c in df.columns):
        sty = sty.map(pnl_css, subset=[c]).format(fmt, subset=[c], na_rep="–")
    return sty


def chart(fig, key: str) -> None:
    """Plotly 차트 — `charts` 레이아웃의 표면·토큰 색을 그대로 쓴다."""
    st.plotly_chart(fig, theme=None, width="stretch", key=key)
