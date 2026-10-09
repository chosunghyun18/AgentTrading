"""거래 상세 — 워크포워드 검증 거래 또는 라운드트립 산출물이 있는 구간의 run → 거래 표 → 거래 전후 1분봉 캔들."""

import streamlit as st

from src.ingest.store import MissingDaysError
from src.ui import charts, common
from src.ui import data as ud

common.setup("거래 상세")
SRC_WF, SRC_SPAN = "워크포워드 검증 거래", "백테스트 구간 run"
source = st.radio("거래 출처", (SRC_WF, SRC_SPAN), horizontal=True, key="tr-source")

trades = None
view_id = source  # 행 번호 위젯 key — 거래 목록이 바뀌면 새 위젯(이전 번호가 새 목록 범위를 넘지 않게)
if source == SRC_WF:
    name = common.select_report()
    if name is None:
        common.no_report_notice()
        st.stop()
    trades = common.wf_trades(name)
    view_id += f"-{name}"
    if trades is None:
        common.no_equity_notice(name)
        st.stop()
    if ud.equity_stale(common.path("AT_OUT_DIR"), name):
        common.stale_notice(name)
    st.caption(f"{name} · default 프로필 · 폴드별 선택 run 의 검증 구간 거래 {len(trades):,}건")
else:
    spans = ud.list_roundtrip_spans(common.path("AT_OUT_DIR"))
    if spans.empty:
        st.info("라운드트립 산출물이 있는 구간이 없다.")
        st.stop()
    c1, c2 = st.columns(2)
    profiles = sorted(spans["profile"].unique())
    profile = c1.selectbox("프로필", profiles, index=common.default_index(profiles), key="tr-profile")
    span = c2.selectbox("구간", spans.loc[spans["profile"] == profile, "span"].tolist(), key="tr-span")
    sums = ud.list_summaries(common.path("AT_OUT_DIR"))
    hit = sums[(sums["profile"] == profile) & (sums["span"] == span)]
    if hit.empty:
        st.info(f"`summary/{profile}/{span}.json` 이 없어 run 목록을 만들 수 없다.")
        st.stop()
    _, runs = common.summary(hit["path"].iloc[0])
    runs = runs[runs["n_trades"] > 0].sort_values("sharpe", ascending=False, na_position="last")
    if runs.empty:
        st.info(f"{profile} · {span} 구간에는 거래가 있는 run 이 없다.")
        st.stop()
    labels = [f"{r.param_id} · Sharpe {r.sharpe:.2f} · 거래 {r.n_trades} · {r.gate}" for r in runs.itertuples()]
    i = st.selectbox("run (Sharpe 내림차순)", range(len(labels)), format_func=lambda k: labels[k], key="tr-run")
    r = runs.iloc[i]
    trades = common.roundtrips(profile, span, r["strategy_id"], r["param_id"])
    view_id += f"-{profile}-{span}-{r['strategy_id']}-{r['param_id']}"

with common.card("trade-table"):
    st.dataframe(common.trade_table(trades), width="stretch", height=320, key="trade-table",
                 column_config=common.TRADE_CONFIG)

if trades.empty:
    st.stop()
c1, c2 = st.columns([1, 2])
k = int(c1.number_input("거래 행 번호 (표의 왼쪽 번호)", min_value=0, max_value=len(trades) - 1, value=0,
                        key=f"tr-row-{view_id}"))
pad = c2.slider("전후 표시 시간(시간)", min_value=1, max_value=ud.MAX_PAD_HOURS, value=6, key="tr-pad")
t = trades.iloc[k]

m = st.columns(5)
m[0].metric("방향", "롱" if t["side"] == "long" else "숏")
m[1].metric("net", f"{t['net_ret']:.2%}")
m[2].metric("보유", f"{t['holding_min']:.0f}분")
m[3].metric("청산 사유", str(t["exit_reason"]))
m[4].metric("펀딩", f"{int(t['n_funding'])}회" if "n_funding" in t else "미반영")

with common.card("candle"):
    try:
        bars = common.trade_bars(t["entry_ts"], t["exit_ts"], float(pad))
    except MissingDaysError as e:
        st.warning(f"1분봉 결측으로 캔들을 그릴 수 없다: {e}")
    except ValueError as e:
        st.warning(f"표본 밖 구간이라 읽지 않는다: {e}")
    else:
        common.chart(charts.candle_fig(bars, t.to_dict()), "candle")
        st.caption(f"{t['entry_ts']} ~ {t['exit_ts']} 전후 {pad}시간 · 1분봉 {len(bars):,}개")
