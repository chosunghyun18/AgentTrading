"""Read-only dashboard presentation from the selected report; no exchange calls."""

from __future__ import annotations

import html
import math
from datetime import datetime, timezone

import streamlit as st

from src.ui import common, theme
from src.ui import data as ud

PROFILE_LABELS = {"default": "판정 · 기본 비용", "bybit": "민감도 · Bybit 비용"}

NEXT_STEPS = {
    "pass": ("OOS 판정 준비", "워크포워드 관문을 통과했다. 백테스트 → 워크포워드에서 근거를 확인하고 OOS 1회 판정을 준비한다. 페이퍼 검증과 사람 승인 전에는 실거래를 시작하지 않는다."),
    "fail": ("미달 원인 확인", "백테스트 → 진단에서 실패한 지표와 비용·펀딩 영향을 확인한다. 다음 가설은 전략 화면에서 검토한다."),
    "insufficient": ("거래 표본 확인", "백테스트 → 전략 탐색에서 거래 수와 필터 조건을 확인한 뒤 새 검증을 계획한다."),
}


def next_step(rep: dict | None) -> None:
    """Give the operator one explicit next action without changing the research verdict."""
    if rep is None:
        title = "첫 검증 만들기"
        detail = "백테스트 → 워크포워드에서 필요한 산출물과 실행 방법을 확인한다."
    else:
        title, detail = NEXT_STEPS.get(rep.get("verdict"), ("판정 확인", "백테스트 → 워크포워드에서 리포트 상태와 실행 조건을 확인한다."))
    st.markdown(f'<div class="next-step"><span class="eyebrow">다음 단계</span>'
                f'<strong>{html.escape(title)}</strong><span>{html.escape(detail)}</span></div>',
                unsafe_allow_html=True)


def context(name: str, rep: dict) -> None:
    """Keep symbol, sample and source timestamp beside the report selector."""
    p = ud.report_path(common.path("AT_OUT_DIR"), name)
    try:
        updated = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except OSError:
        updated = "확인 불가"
    meta = rep.get("meta", {})
    fields = [("시장", meta.get("symbol") or common.symbol()), ("리포트", name),
              ("검증 표본", " ~ ".join(map(str, rep["sample"]))), ("파일 갱신", updated)]
    body = "".join(f"<span>{html.escape(k)} <b>{html.escape(str(v))}</b></span>" for k, v in fields)
    st.markdown(f'<div class="research-context">{body}</div>', unsafe_allow_html=True)


def _number(value, fmt: str) -> str:
    if value is None or not math.isfinite(float(value)):
        return "–"
    return format(float(value), fmt)


def performance(rep: dict, profile: str) -> None:
    """Report statistics stay authoritative even when the curve export is absent."""
    stats = rep["stitched"][profile]
    values = [("검증 누적 수익률", _number(stats.get("total_net_ret"), "+.2%")),
              ("최대 낙폭", _number(stats.get("mdd"), ".2%")),
              ("검증 거래", _number(stats.get("n_trades"), ",.0f")),
              ("검증 일 수", _number(stats.get("n_days"), ",.0f"))]
    for col, (label, value) in zip(st.columns(4), values):
        col.metric(label, value)


def decision(rep: dict, profile: str) -> None:
    """A cost-sensitivity pass must never imply authorization to trade."""
    st.subheader("검증 상태")
    verdict = rep.get("verdict")
    st.markdown(theme.badge_html("판정 " + theme.verdict_label(verdict), theme.verdict_color(verdict)),
                unsafe_allow_html=True)
    kpis = ud.gate_kpis(rep, profile)
    passed = sum(k["ok"] for k in kpis)
    st.progress(passed / len(kpis), text=f"{PROFILE_LABELS[profile]} · 기준 {passed}/{len(kpis)} 충족")
    if profile == "bybit":
        st.caption("Bybit 비용 민감도 결과다. 전략 승인 판정은 기본 비용 결과를 따른다.")
    if verdict == "pass":
        st.write("다음: OOS 1회 판정 → 페이퍼 8주 → 사람 승인")
    else:
        failed = [k["label"] for k in ud.gate_kpis(rep, "default") if not k["ok"]]
        st.write("다음: 진단 화면에서 " + (" · ".join(failed) if failed else "판정 리포트") + " 확인")
    st.caption("백테스트 검증 결과 · 실제 계정 수익률과 별개")


def empty_state() -> None:
    next_step(None)
    with common.card("getting-started"):
        st.subheader("첫 검증 리포트 준비")
        st.write("전략 → 워크포워드 검증 → 결과 확인 순서로 시작한다.")
        common.no_report_notice()
        st.caption("리포트가 생성되면 수익률·낙폭·관문 결과가 여기에 표시된다.")
