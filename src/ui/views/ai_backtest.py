"""AI 백테스트 — 말로 물어보면 명세(JSON)로 바꿔 ETF 일봉 엔진으로 돌리고 단위가치 지수·낙폭을 그린다.

- AI 는 번역만(`src/aibt/llm.py`), 계산은 `src/aibt/etf.py`. 같은 명세 JSON 은 `st.cache_data` 로 재계산을 생략한다.
- 쓰기 없음(결과는 세션 상태, 다운로드 버튼으로 반출). 네트워크 호출은 사용자가 보낼 때만. LLM 호출은 세션당 상한.
- 키·SDK 가 없으면 JSON 직접 입력 경로만 열린다.
설계: Obsidian `Projects/work/AgentTrading/task/ai-backtest.md`.
"""

from __future__ import annotations

import json
import math

import pandas as pd
import streamlit as st

from src.aibt import etf, llm
from src.aibt import spec as spec_mod
from src.shared import settings
from src.ui import charts, common, theme

MAX_LLM_CALLS = 30
EXAMPLE = {
    "title": "QQQ −7/−30/−45% 트리거로 TQQQ 매수 (QLD 코어)", "start": "2010-01-01", "end": "", "initial_cash": 10000,
    "monthly_contribution": 0, "benchmark": "QQQ", "rule": "drawdown_trigger",
    "weights": [{"symbol": "QLD", "weight": 1.0}], "signal_symbol": "", "ma_window": 0, "hold_symbol": "", "else_symbol": "",
    "ref_symbol": "QQQ", "stages": [{"drawdown_pct": 7, "cumulative_fraction": 0.25}, {"drawdown_pct": 30, "cumulative_fraction": 0.6},
                                    {"drawdown_pct": 45, "cumulative_fraction": 1.0}],
    "vehicle": "TQQQ", "check": "eom", "reset_years": 0, "reserve_fraction": 0.3, "rebalance_period": "monthly",
}
PROMPTS = ["QQQ를 2010년부터 그냥 들고 있기, 벤치마크 없이", "매달 100씩 QLD 적립, 2015년부터",
           "QQQ가 200일선 위면 TQQQ, 아래면 현금", "QQQ 60% TQQQ 40% 매년 리밸런싱, 2012년부터"]

common.setup("AI 백테스트")


@st.cache_data(show_spinner=False, ttl=600)
def _catalog(url: str) -> dict:
    return etf.catalog(url)


@st.cache_data(show_spinner=False, max_entries=32)
def _run(spec_json: str, url: str) -> etf.Result:
    sp = spec_mod.validate(json.loads(spec_json), _catalog(url))
    return etf.run(sp, etf.load_prices(url, sp.symbols))


def _pct(v) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1%}"


def _dumps(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=1)


try:
    url = settings.etf_db_url()
except ValueError as e:
    st.error(f"시세 DB 설정 오류: {e}")
    st.stop()
if not url:
    st.info("시세 DB(`AT_MARKET_DB_URL`)가 비어 있어 ETF 일봉을 읽을 수 없다. DEPLOY.md 6절대로 설정한다.")
    st.stop()
try:
    catalog = _catalog(url)
except Exception as e:  # 접속 실패·테이블 없음 — 사유를 그대로 보인다(비밀번호는 URL 에만 있고 메시지에는 없다)
    st.error(f"시세 DB 를 읽지 못했다: {type(e).__name__}")
    st.stop()
if not catalog:
    st.info("`prices_daily` 가 비어 있다. MiroFishTrader `python3 -m src.sources.price_history --mysql` 로 적재한다.")
    st.stop()

ss = st.session_state
ss.setdefault("ai_turns", [])          # LLM 에 보내는 대화(role·content)
ss.setdefault("ai_chat", [])           # 화면용 말풍선(role·text)
ss.setdefault("ai_spec_json", _dumps(EXAMPLE))
ss.setdefault("ai_result_key", "")     # 마지막으로 실행한 명세 JSON
ss.setdefault("ai_calls", 0)
ss.setdefault("ai_usage", [])

ai_on = llm.available()
ai_status = llm.status()
with st.expander("데이터 범위 · 쓰는 법", expanded=not ss["ai_result_key"]):
    st.dataframe(pd.DataFrame([{"종목": s, "시작": a, "끝": b} for s, (a, b) in sorted(catalog.items())]),
                 hide_index=True, width="stretch")
    st.markdown("AI 는 요청을 **명세(JSON)** 로 바꾸기만 하고, 계산은 이 서버의 엔진이 한다. 같은 명세는 늘 같은 결과다. "
                "그래프와 지표는 **단위가치 지수(시작 = 1.0, 시간가중)** 라 적립금에 가려지지 않는다. "
                "신호는 종가로 판정해 다음 거래일 종가에 체결, 가격은 배당 재투자 기준.")
    if ai_on:
        st.caption(f"AI 번역: **{ai_status['label']}** · 모델 `{ai_status['model']}`"
                   + (" — 무료 모델만 호출하도록 가격 상한 0·폴백 금지를 걸고, 응답 비용이 0 이 아니면 결과를 버린다."
                      if ai_status["provider"] == "openrouter_free" else ""))
    else:
        st.info("AI 입력이 꺼져 있다 — 아래 명세 JSON 을 직접 고쳐 실행할 수 있다. 자연어로 쓰려면 둘 중 하나를 `.env` 에 넣는다:\n\n"
                f"- **무료(권장)**: `{ai_status['free_key']}` — openrouter.ai 에서 발급. 추가 설치 없이 바로 된다.\n"
                f"- 유료: `{ai_status['paid_key']}`"
                + ("" if ai_status["sdk"] else " + `uv sync --extra ai`"))
    if ai_status["error"]:
        st.warning(ai_status["error"])

# ── 대화 ───────────────────────────────────────────────────────────────────────────────────────────
for m in ss["ai_chat"]:
    with st.chat_message(m["role"]):
        st.markdown(m["text"])

if ai_on and not ss["ai_chat"]:
    st.caption("예: " + " · ".join(f"“{p}”" for p in PROMPTS))

prompt = st.chat_input("백테스트를 말로 설명한다 — 예: QQQ가 고점 대비 30% 빠지면 TQQQ 산다, 2010년부터",
                       disabled=not ai_on or ss["ai_calls"] >= MAX_LLM_CALLS, key="ai_prompt")
if ss["ai_calls"] >= MAX_LLM_CALLS:
    st.warning(f"이 세션의 AI 호출 상한({MAX_LLM_CALLS}회)에 닿았다. 페이지를 새로 열거나 JSON 을 직접 고친다.")

if prompt:
    ss["ai_chat"].append({"role": "user", "text": prompt})
    turns = ss["ai_turns"] + [{"role": "user", "content": prompt}]
    with st.spinner("명세로 바꾸는 중…"):
        pr = llm.parse(turns, catalog)
    ss["ai_calls"] += 1
    if pr.usage:
        ss["ai_usage"].append({"model": pr.model, **pr.usage})
    if pr.error:
        ss["ai_chat"].append({"role": "assistant", "text": f"⚠️ {pr.error}"})
    else:
        try:
            sp = spec_mod.validate(pr.raw, catalog)
            ss["ai_turns"] = turns + [{"role": "assistant", "content": pr.text}]
            ss["ai_spec_json"] = _dumps(sp.to_dict())
            ss["ai_result_key"] = ss["ai_spec_json"]
            ss["ai_chat"].append({"role": "assistant", "text": f"**{sp.title}**\n\n{spec_mod.describe(sp)}"})
        except spec_mod.SpecError as e:
            ss["ai_spec_json"] = _dumps(pr.raw)
            ss["ai_chat"].append({"role": "assistant", "text": "명세가 검증을 통과하지 못했다 — 아래 JSON 을 고쳐 실행한다:\n- "
                                                                + "\n- ".join(e.errors)})
    st.rerun()

# ── 명세 편집기 ───────────────────────────────────────────────────────────────────────────────────
with st.expander("명세 JSON — 보기·수정·실행", expanded=not ai_on or not ss["ai_result_key"]):
    edited = st.text_area("명세", value=ss["ai_spec_json"], height=320, key="ai_spec_editor", label_visibility="collapsed")
    c1, c2 = st.columns([1, 3])
    if c1.button("이 명세로 실행", type="primary", key="ai_run_spec"):
        try:
            sp = spec_mod.validate(json.loads(edited), catalog)
            ss["ai_spec_json"] = _dumps(sp.to_dict())
            ss["ai_result_key"] = ss["ai_spec_json"]
            st.rerun()
        except json.JSONDecodeError as e:
            st.error(f"JSON 문법 오류: {e}")
        except spec_mod.SpecError as e:
            st.error("명세 오류:\n- " + "\n- ".join(e.errors))
    c2.caption("규칙·키 설명은 `src/aibt/spec.py` 의 SCHEMA. 모든 키는 필수이고 안 쓰는 키는 \"\"·0·[] 로 둔다.")

# ── 결과 ───────────────────────────────────────────────────────────────────────────────────────────
if not ss["ai_result_key"]:
    st.stop()
try:
    res = _run(ss["ai_result_key"], url)
except (ValueError, spec_mod.SpecError) as e:
    st.error(f"실행할 수 없다: {e}")
    st.stop()
sp, m = res.spec, res.metrics
st.subheader(sp.title)
st.caption(spec_mod.describe(sp))
for n in res.notes:
    st.info(n)

with common.card("ai_equity"):
    frames = [pd.DataFrame({"date": res.unit.index, "profile": "전략", "equity": res.unit.values})]
    if res.benchmark_unit is not None:
        frames.append(pd.DataFrame({"date": res.benchmark_unit.index, "profile": f"벤치마크 {sp.benchmark}",
                                    "equity": res.benchmark_unit.values}))
    fig = charts.equity_fig(pd.concat(frames, ignore_index=True))
    fig.update_layout(title_text="단위가치 지수 (시작 = 1.0) · 낙폭")
    if len(frames) == 2:  # equity_fig 는 프로필명(default·bybit)으로만 색을 고른다 → 벤치마크 선을 두 번째 색으로
        fig.data[1].line.color = theme.tokens()["LINE_COLORS"]["bybit"]
    common.chart(fig, "ai_equity_chart")

cols = st.columns(6)
cols[0].metric("최종 평가액", f"{m['final_equity']:,.0f}", help=f"총납입 {m['contributed']:,.0f} → 배수 {m['multiple']:.2f}x")
cols[1].metric("단위가치 배수", f"{m['unit_multiple']:.2f}x", help="시간가중 — 적립 시점과 무관한 전략 자체의 성과")
cols[2].metric("연환산 수익률", _pct(m["cagr"]), help=f"{m['years']:.1f}년")
cols[3].metric("최대 낙폭", _pct(m["mdd"]), help=f"저점 {m['mdd_date']}")
cols[4].metric("연 변동성", _pct(m["vol"]))
cols[5].metric("매매 횟수", f"{m['n_trades']}", help=f"매수 {m['n_buys']}회")
if "benchmark" in m:
    b = m["benchmark"]
    st.caption(f"벤치마크 {sp.benchmark} 단순 보유(같은 현금 흐름): 단위가치 {b['unit_multiple']:.2f}x · 연환산 {_pct(b['cagr'])} · "
               f"최대 낙폭 {_pct(b['mdd'])} · 변동성 {_pct(b['vol'])}")

left, right = st.columns([1, 1])
with left:
    st.markdown("**연도별 수익률** (단위가치 기준, 첫·마지막 해는 부분)")
    st.dataframe(res.yearly.rename_axis("연도").style.format("{:+.1%}"), width="stretch")
with right:
    st.markdown(f"**매매 기록** {len(res.trades)}건")
    tr = res.trades.copy()
    if not tr.empty:
        tr["date"] = pd.to_datetime(tr["date"]).dt.date
    st.dataframe(tr.rename(columns={"date": "날짜", "action": "구분", "symbol": "종목", "amount": "금액", "reason": "사유"}),
                 hide_index=True, width="stretch", height=300,
                 column_config={"금액": st.column_config.NumberColumn(format="%.0f")})

d1, d2, _ = st.columns([1, 1, 3])
curve = pd.DataFrame({"date": res.unit.index.date, "equity": res.equity.values, "contributed": res.contributed.values,
                      "unit": res.unit.values})
if res.benchmark_unit is not None:
    curve["benchmark_unit"] = res.benchmark_unit.values
d1.download_button("자본곡선 CSV", curve.to_csv(index=False).encode("utf-8"), "ai_backtest_curve.csv", "text/csv", key="ai_dl_csv")
d2.download_button("명세 JSON", ss["ai_result_key"].encode("utf-8"), "ai_backtest_spec.json", "application/json", key="ai_dl_json")

if ss["ai_usage"]:
    with st.expander(f"AI 토큰 사용 — 이 세션 {ss['ai_calls']}회"):
        u = pd.DataFrame(ss["ai_usage"])
        st.dataframe(u.rename(columns={"model": "모델", "input": "입력", "cache_read": "캐시 읽기", "cache_write": "캐시 쓰기",
                                       "output": "출력", "cost": "비용($)"}), hide_index=True, width="stretch")
        st.caption("무료 공급자는 '비용($)' 이 늘 0 이다(0 이 아니면 호출을 실패로 처리한다). "
                   "Anthropic 경로에서는 시스템 프롬프트가 캐시돼 두 번째 호출부터 '캐시 읽기'에 잡힌다.")
