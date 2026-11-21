"""AI 백테스트 — **대화창 하나**로 쓰는 화면. 세팅은 화면이 아니라 JSON 파일(`AT_AIBT_CONFIG`)에 있다.

- 말로 설명하면 AI 가 **명세(JSON)로 번역만** 하고, 계산은 `src/aibt/etf.py` 가 결정적으로 한다.
- 세팅 파일의 프리셋 이름을 그대로 치면 **AI 없이** 바로 돈다(키가 없어도 쓰는 경로).
- 화면은 세팅 파일을 **읽기만** 한다. 쓰기 없음(파일·DB 둘 다). 네트워크 호출은 사용자가 보낼 때만.
설계: Obsidian `Projects/work/AgentTrading/task/ai-backtest.md` "다음 방향".
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import streamlit as st

from src.aibt import etf, llm
from src.aibt import spec as spec_mod
from src.aibt import userconfig as uc
from src.shared import settings
from src.ui import charts, common, theme

MAX_LLM_CALLS = 30
HELP = ("말로 설명하면 백테스트를 돌린다. 예: **QQQ가 고점 대비 30% 빠지면 TQQQ 산다, 2015년부터** · "
        "**매달 100씩 QLD 적립** · **QQQ 200일선 위면 TQQQ, 아래면 현금**\n\n"
        "명세 JSON 을 그대로 붙여 넣어도 바로 돈다(AI 호출 없음).")

common.setup("AI 백테스트")


@st.cache_data(show_spinner=False, ttl=600)
def _catalog(url: str) -> dict:
    return etf.catalog(url)


@st.cache_data(show_spinner=False, max_entries=32)
def _run(spec_json: str, url: str) -> etf.Result:
    sp = spec_mod.validate(json.loads(spec_json), _catalog(url))
    return etf.run(sp, etf.load_prices(url, sp.symbols))


@st.cache_data(show_spinner=False, max_entries=4)
def _user_config(path_str: str, mtime: float) -> uc.UserConfig:
    """mtime 을 캐시 키에 넣어 파일을 고치면 자동 반영한다(`common.py` 의 산출물 캐시와 같은 방식)."""
    return uc.load(Path(path_str))


def _pct(v) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1%}"


def _dumps(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=1)


def _say(role: str, text: str) -> None:
    st.session_state["ai_chat"].append({"role": role, "text": text})


def _show_path(p: Path) -> str:
    """화면·대화에 쓸 경로 — 가능하면 저장소 상대경로(절대경로에는 사용자 이름이 들어간다)."""
    try:
        return str(p.relative_to(settings.ROOT))
    except ValueError:
        return str(p)


# ── 시세 DB ──────────────────────────────────────────────────────────────────────────────────────
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
except Exception as e:  # 접속 실패·테이블 없음 — 사유만 보인다(URL 의 비밀번호는 메시지에 없다)
    st.error(f"시세 DB 를 읽지 못했다: {type(e).__name__}")
    st.stop()
if not catalog:
    st.info("`prices_daily` 가 비어 있다. MiroFishTrader `python3 -m src.sources.price_history --mysql` 로 적재한다.")
    st.stop()

# ── 사용자 세팅 파일 ─────────────────────────────────────────────────────────────────────────────
cfg_path = uc.path()
cfg_show = _show_path(cfg_path)
cfg_error = ""
try:
    mtime = cfg_path.stat().st_mtime
except OSError:
    mtime = 0.0  # 없거나 읽을 수 없다 — `uc.load` 가 사유를 가린다
try:
    cfg = _user_config(str(cfg_path), mtime)
except uc.UserConfigError as e:
    cfg, cfg_error = uc.UserConfig(path=cfg_path, exists=mtime > 0), "\n- ".join(e.errors)

ss = st.session_state
ss.setdefault("ai_turns", [])
ss.setdefault("ai_chat", [])
ss.setdefault("ai_result_key", "")
ss.setdefault("ai_calls", 0)
ss.setdefault("ai_usage", [])

ai_on = llm.available()
st_status = llm.status()
span = f"{min(a for a, _ in catalog.values())} ~ {max(b for _, b in catalog.values())}"
line = [f"종목 {' · '.join(sorted(catalog))} · {span}",
        f"AI {st_status['label']}" if ai_on else "AI 꺼짐",
        f"세팅 `{cfg_show}`" + ("" if cfg.exists else " (없음)")]
if cfg.presets:
    line.append(f"프리셋 {len(cfg.presets)}개")
st.caption(" · ".join(line))

if cfg_error:
    st.warning(f"세팅 파일을 읽지 못해 기본값으로 돈다 — `{cfg_show}`\n- {cfg_error}")
if st_status["error"]:
    st.warning(st_status["error"])

# 첫 화면 인사 — 쓰는 법·프리셋·키 안내를 전부 대화 안에서 한다
if not ss["ai_chat"]:
    msg = [HELP]
    if cfg.presets:
        msg.append("저장된 프리셋(이름만 쳐도 **AI 없이** 바로 실행): " + " · ".join(f"`{n}`" for n in cfg.names))
    if not ai_on:
        msg.append(f"자연어를 쓰려면 `{st_status['free_key']}`(openrouter.ai 무료)를 `.env` 에 넣는다. "
                   "지금은 프리셋 이름만 실행할 수 있다.")
    if not cfg.exists:
        msg.append(f"세팅(기본 자본·선호·프리셋)은 `{cfg_show}` 에 둔다. "
                   f"`{uc.example_path(cfg_path).name}` 를 복사해 쓴다.")
    _say("assistant", "\n\n".join(msg))

# ── 대화 ─────────────────────────────────────────────────────────────────────────────────────────
for m in ss["ai_chat"]:
    with st.chat_message(m["role"]):
        st.markdown(m["text"])

blocked = ai_on and ss["ai_calls"] >= MAX_LLM_CALLS
if blocked:
    st.warning(f"이 세션의 AI 호출 상한({MAX_LLM_CALLS}회)에 닿았다. 페이지를 새로 열거나 프리셋 이름을 친다.")
prompt = st.chat_input("말로 설명하거나, 프리셋 이름 또는 명세 JSON 을 붙여 넣는다", key="ai_prompt")

def _accept(sp, note: str, turns: list[dict] | None = None) -> None:
    """검증을 통과한 명세를 결과로 삼는다."""
    ss["ai_result_key"] = _dumps(sp.to_dict())
    ss["ai_turns"] = turns if turns is not None else [
        {"role": "user", "content": f"'{sp.title}' 로 실행"},
        {"role": "assistant", "content": json.dumps(sp.to_dict(), ensure_ascii=False)}]
    _say("assistant", note)


def _fail(note: str) -> None:
    """실패한 턴 — 이전 결과를 지운다(오류 문구 아래 옛 차트가 남지 않게)."""
    ss["ai_result_key"] = ""
    _say("assistant", note)


if prompt:
    _say("user", prompt)
    preset = uc.find_preset(cfg, prompt)
    typed_spec = llm.extract_json(prompt)  # 대화창에 명세 JSON 을 그대로 붙여 넣는 경로(키 없이도 쓴다)
    if preset:  # 프리셋 직행 — AI 호출 없음
        try:
            sp = spec_mod.validate(uc.complete_spec(cfg, preset), catalog)
            _accept(sp, f"프리셋 **{preset}** 을 그대로 실행했다(AI 호출 없음).\n\n{spec_mod.describe(sp)}")
        except spec_mod.SpecError as e:
            _fail(f"프리셋 **{preset}** 이 올바르지 않다 — `{cfg_show}` 에서 고친다:\n- " + "\n- ".join(e.errors))
    elif typed_spec is not None:  # 명세 JSON 직접 입력 — AI 호출 없음
        try:
            sp = spec_mod.validate(typed_spec, catalog)
            _accept(sp, f"붙여 넣은 명세를 그대로 실행했다(AI 호출 없음).\n\n{spec_mod.describe(sp)}")
        except spec_mod.SpecError as e:
            _fail("붙여 넣은 명세가 검증을 통과하지 못했다:\n- " + "\n- ".join(e.errors))
    elif not ai_on:
        names = (" 저장된 프리셋: " + " · ".join(f"`{n}`" for n in cfg.names)) if cfg.presets else ""
        _fail(f"AI 가 꺼져 있어 자연어를 번역할 수 없다. `{st_status['free_key']}` 를 `.env` 에 넣거나, "
              f"프리셋 이름 또는 명세 JSON 을 그대로 친다.{names}")
    elif blocked:
        _fail("AI 호출 상한에 닿았다. 페이지를 새로 열거나 프리셋 이름·명세 JSON 을 친다.")
    else:
        turns = ss["ai_turns"] + [{"role": "user", "content": prompt}]
        with st.spinner("명세로 바꾸는 중…"):
            pr = llm.parse(turns, catalog, user_block=uc.prompt_block(cfg))
        ss["ai_calls"] += 1
        if pr.usage:
            ss["ai_usage"].append({"model": pr.model, **pr.usage})
        if pr.error:
            _fail(f"⚠️ {pr.error}")
        else:
            try:
                sp = spec_mod.validate(pr.raw, catalog)
                _accept(sp, f"**{sp.title}**\n\n{spec_mod.describe(sp)}",
                        turns=turns + [{"role": "assistant", "content": pr.text}])
            except spec_mod.SpecError as e:
                # 화면에 편집기를 두지 않는다 → 사유와 받은 JSON 을 대화에 보여 고쳐 말하거나 그대로 붙여 넣게 한다.
                # 실패한 답도 대화 맥락에 남겨 다음 턴이 무엇이 틀렸는지 알게 한다.
                ss["ai_turns"] = turns + [{"role": "assistant", "content": pr.text}]
                _fail("명세가 검증을 통과하지 못했다. 다시 말해 주거나, 아래를 고쳐 그대로 붙여 넣거나 "
                      f"`{cfg_show}` 의 `presets` 에 넣는다:\n- " + "\n- ".join(e.errors)
                      + f"\n\n````json\n{_dumps(pr.raw)}\n````")
    st.rerun()

# ── 결과 ─────────────────────────────────────────────────────────────────────────────────────────
if not ss["ai_result_key"]:
    st.stop()
try:
    res = _run(ss["ai_result_key"], url)
except (ValueError, spec_mod.SpecError) as e:
    st.error(f"실행할 수 없다: {e}")
    st.stop()
sp, m = res.spec, res.metrics
for n in res.notes:
    st.info(n)

with common.card("ai_equity"):
    frames = [pd.DataFrame({"date": res.unit.index, "profile": "전략", "equity": res.unit.values})]
    if res.benchmark_unit is not None:
        frames.append(pd.DataFrame({"date": res.benchmark_unit.index, "profile": f"벤치마크 {sp.benchmark}",
                                    "equity": res.benchmark_unit.values}))
    fig = charts.equity_fig(pd.concat(frames, ignore_index=True))
    fig.update_layout(title_text=f"{sp.title} — 단위가치 지수 (시작 = 1.0) · 낙폭")
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
st.caption(f"이 명세를 `{cfg_show}` 의 `presets` 에 넣으면 다음부터 이름만 쳐서 AI 없이 돌릴 수 있다.")

if ss["ai_usage"]:
    with st.expander(f"AI 토큰 사용 — 이 세션 {ss['ai_calls']}회"):
        u = pd.DataFrame(ss["ai_usage"])
        st.dataframe(u.rename(columns={"model": "모델", "input": "입력", "cache_read": "캐시 읽기", "cache_write": "캐시 쓰기",
                                       "output": "출력", "cost": "비용($)"}), hide_index=True, width="stretch")
        st.caption("무료 공급자는 '비용($)' 이 늘 0 이다(0 이 아니면 호출을 실패로 처리한다). "
                   "Anthropic 경로에서는 시스템 프롬프트가 캐시돼 두 번째 호출부터 '캐시 읽기'에 잡힌다.")
