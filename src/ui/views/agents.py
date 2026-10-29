"""에이전트 — 등록 에이전트·run 목록·자본곡선·이벤트. 저장소(SQLite)만 읽고, 쓰기는 정지 명령뿐.

시작은 CLI(`python -m src.cli.at run <agent>`, shadow·paper 만). 모든 run 은 evidence=0 — 게이트 증거가 아니다.
backtest(XBT, 시작 = 1.0) 와 shadow·paper(USDT) 는 단위가 달라 한 그래프에 겹치지 않는다.
"""

import pandas as pd
import streamlit as st

from src.store import db
from src.ui import common

common.setup("에이전트")
con = db.connect()
agents = db.list_agents(con)
if not agents:
    st.info("등록된 에이전트가 없다. `python -m src.cli.at agent add <yaml>` 로 등록한다(README '플랫폼 CLI').")
    st.stop()

with common.card("agents"):
    st.subheader("에이전트")
    st.dataframe(pd.DataFrame(agents), hide_index=True, width="stretch")

agent_id = st.selectbox("에이전트", [a["id"] for a in agents], key="agent")
runs = db.list_runs(con, agent_id)
running = [r for r in runs if r["status"] == "running" and r["kind"] != "backtest"]
if running:
    st.warning(f"실행 중: run {running[0]['id']} ({running[0]['kind']})")
    if st.button("정지 명령 보내기", key="stop", help="다음 봉(최대 1분)에서 런타임이 처리한다. paper 는 포지션을 시장가로 정리한다"):
        db.enqueue_command(con, agent_id, "stop")
        st.success("정지 명령을 등록했다")

if not runs:
    st.info("이 에이전트의 run 이 아직 없다. `python -m src.cli.at backtest|run` 으로 만든다.")
    st.stop()

with common.card("runs"):
    st.subheader("실행 이력")
    st.dataframe(pd.DataFrame(runs)[["id", "kind", "status", "evidence", "started_at", "finished_at"]],
                 hide_index=True, width="stretch")

run_id = st.selectbox("run", [r["id"] for r in runs], key="run",
                      format_func=lambda i: next(f"{r['id']} · {r['kind']} · {r['status']}" for r in runs if r["id"] == i))
run = next(r for r in runs if r["id"] == run_id)
eq = pd.DataFrame(db.equity(con, run_id))
with common.card("equity"):
    st.subheader("자본곡선 (" + ("XBT, 시작 = 1.0" if run["kind"] == "backtest" else "USDT") + ")")
    if len(eq):
        eq["ts"] = pd.to_datetime(eq["ts"], utc=True)
        st.line_chart(eq.set_index("ts")["equity"])
    else:
        st.caption("기록 없음")
if run["summary_json"]:
    with st.expander("요약"):
        st.json(run["summary_json"])
ev = db.events(con, run_id, 200)
with common.card("events"):
    st.subheader("이벤트 (최근 200)")
    st.dataframe(pd.DataFrame(ev) if ev else pd.DataFrame(columns=["ts", "kind"]), hide_index=True, width="stretch")
