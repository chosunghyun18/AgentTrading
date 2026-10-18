"""전략 탐색 — 구간·프로필 하나의 run 그리드(최대 2,268) 표·필터·Sharpe–MDD 산점도."""

import streamlit as st

from src.ui import charts, common
from src.ui import data as ud
from src.ui import theme

common.setup("전략 탐색")
sums = ud.list_summaries(common.path("AT_OUT_DIR"))
if sums.empty:
    st.info("구간 run 요약이 없다. 먼저 백테스트를 실행한다: "
            "`python -m src.backtest.run --start YYYY-MM-DD --end YYYY-MM-DD --funding`")
    st.stop()

c1, c2 = st.columns(2)
profiles = sorted(sums["profile"].unique())
profile = c1.selectbox("프로필", profiles, index=common.default_index(profiles), key="ex-profile")
spans = sums[sums["profile"] == profile]
span = c2.selectbox("구간", spans["span"].tolist(), key="ex-span")
meta, runs = common.summary(spans.loc[spans["span"] == span, "path"].iloc[0])

f1, f2, f3 = st.columns(3)
triggers = f1.multiselect("트리거", sorted(runs["trigger"].unique()), default=sorted(runs["trigger"].unique()),
                          key="ex-trigger")
gates = f2.multiselect("판정", sorted(runs["gate"].unique()), default=sorted(runs["gate"].unique()), key="ex-gate")
min_trades = f3.number_input("최소 거래 수", min_value=0, value=0, step=10, key="ex-min-trades")
view = runs[runs["trigger"].isin(triggers) & runs["gate"].isin(gates) & (runs["n_trades"] >= min_trades)]

n_pass = int((view["gate"] == "pass").sum())
st.markdown(f"{theme.badge_html(f'통과 {n_pass}', theme.SUCCESS)} "
            f"<span class='status-line'>표시 {len(view):,} / 전체 {len(runs):,} run · "
            f"{meta.get('symbol')} · {meta.get('start')} ~ {meta.get('end')}(반열린) · "
            f"수수료 {meta.get('fee_profile')}</span>",
            unsafe_allow_html=True)
st.caption("같은 시장·기간·비용 프로필 안에서만 후보를 비교한다. 이 순위는 단일 구간 탐색 결과이며, 승인 판정은 워크포워드에서 확인한다.")

with common.card("scatter"):
    common.chart(charts.scatter_fig(view, meta["gate"]), "scatter")

with common.card("runs"):
    rank_options = {"Sharpe 높은 순": ("sharpe", False), "net 수익 높은 순": ("total_net_ret", False),
                    "최대 낙폭 낮은 순": ("mdd", True), "거래 많은 순": ("n_trades", False)}
    rank_label = st.selectbox("후보 정렬", list(rank_options), key="ex-rank")
    rank_column, ascending = rank_options[rank_label]
    cols = ["strategy_id", "param_id", "trigger", "n_trades", "sharpe", "mdd", "total_net_ret", "total_gross_ret",
            "gate"] + [c for c in ("n_funding", "total_funding_xbt") if c in view.columns]
    table = view[cols].sort_values([rank_column, "strategy_id", "param_id"],
                                   ascending=[ascending, True, True], na_position="last", kind="stable").copy()
    table.insert(0, "#", range(1, len(table) + 1))
    styled = table.style.apply(
        lambda r: [f"background-color: {theme.PROFIT}22" if r["gate"] == "pass" else ""] * len(r), axis=1)
    st.dataframe(styled, hide_index=True, width="stretch", height=480, key="run-table",
                 column_config={"mdd": common.PCT, "total_net_ret": common.PCT, "total_gross_ret": common.PCT,
                                "sharpe": st.column_config.NumberColumn(format="%.3f")})

with common.card("compare"):
    st.subheader("후보 나란히 보기")
    if table.empty:
        st.info("현재 필터와 일치하는 후보가 없다.")
    else:
        top = table.head(20).copy()
        top["candidate"] = top["strategy_id"] + " · " + top["param_id"]
        choices = top["candidate"].tolist()
        selected = st.multiselect("비교할 후보 · 현재 정렬 상위 20개", choices,
                                  default=choices[:min(3, len(choices))], max_selections=4, key="ex-compare")
        if selected:
            compared = top.set_index("candidate").loc[selected].reset_index()
            compared = compared[["#", "candidate", "n_trades", "sharpe", "mdd", "total_net_ret", "gate"]]
            st.dataframe(compared, hide_index=True, width="stretch", key="compare-table",
                         column_config={"candidate": st.column_config.TextColumn("전략 · 파라미터", width="large"),
                                        "n_trades": "거래 수", "sharpe": st.column_config.NumberColumn("Sharpe", format="%.3f"),
                                        "mdd": st.column_config.NumberColumn("최대 낙폭", format="percent"),
                                        "total_net_ret": st.column_config.NumberColumn("net 수익", format="percent"),
                                        "gate": "구간 판정"})
        else:
            st.caption("후보를 고르면 수익·낙폭·거래 수와 구간 판정을 같은 표에서 볼 수 있다.")
