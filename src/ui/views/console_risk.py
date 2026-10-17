"""리스크 — 한도 설정, 오늘 손익·포지션 한도 사용률, 정지 상태, 킬스위치·재개."""

from datetime import datetime, timezone
import math

import streamlit as st

from src.agent import risk
from src.agent.risk import HARD_MAX_LEVERAGE, RiskLimits
from src.ui import common, theme, trading

common.setup("리스크")
svc = trading.service()
trading.banner(svc)
trading.show_flash()
lim = svc.limits()

with common.card("kill"):
    st.subheader("킬스위치")
    if svc.halted():
        st.markdown(theme.badge_html("정지 중", theme.WARNING) + f" <span class='status-line'>"
                    f"{risk.halt_reason(svc.state_dir) or ''}</span>", unsafe_allow_html=True)
        ok = st.checkbox("원인을 확인했고 신규 주문을 다시 허용한다", key="resume-ok")
        st.button("재개", disabled=not ok, key="resume-btn",
                  on_click=trading.act("재개", svc.resume, "재개했다 — 신규 주문 허용", reset=("resume-ok",)))
    else:
        st.caption("실행하면: 정지 플래그 → 전 미체결 취소 → 포지션 시장가 청산. 재개 전까지 신규 주문 거부.")
    reason = st.text_input("사유", value="수동 킬스위치", key="kill-reason")
    ok = st.checkbox("모든 주문을 취소하고 포지션을 청산한다", key="kill-ok")

    def _kill():
        out = svc.kill_switch(st.session_state.get("kill-reason") or "수동 킬스위치")
        if out["errors"]:
            raise RuntimeError("일부 단계 실패 — 거래소 화면에서 직접 확인한다: " + " / ".join(out["errors"]))

    st.button("킬스위치 실행", type="primary", disabled=not ok, key="kill-btn",
              on_click=trading.act("킬스위치", _kill, "정지·취소·청산 완료", reset=("kill-ok",)))

# Render before the no-key usage stop: desired policy can be saved offline.
with common.card("drawdown"):
    st.subheader("계정 낙폭 보호 (MDD)")
    st.caption("미실현 손익을 포함한 계정 전체 USD equity의 관측 최고점 대비 낙폭이다. "
               "기존 백테스트 MDD·전략 합격 판정과 별개이며 그 기준은 바꾸지 않는다.")
    st.warning("감시는 watch 프로세스가 실행 중이어야 한다. 한도 저장만으로 감시가 켜지지 않으며, "
               "프로세스가 꺼져 있는 동안 보호를 보장하지 않는다. 화면은 자동 감시하지 않는다.")
    st.caption("전용 계정·입출금 없음 전제. 입출금 시 운용을 일시정지하고 포지션·신규 미체결·접수 불명 주문이 "
               "없는 계정에서 사유를 기록해 기준금을 재설정한다. 과거 epoch 이력은 보존한다.")
    dd = {}
    with trading.guarded("낙폭 상태 조회"):
        dd = svc.drawdown_status() or {}
    # A failed explicit poll is a newer observation attempt than local status.
    # Keep its block visible until a successful read or a newer persisted sample.
    poll_error = st.session_state.get("_dd_poll_error")
    if poll_error and poll_error[:3] == (svc.mode, dd.get("epoch"), dd.get("observed_at")):
        dd = {**dd, "blocked": True, "reason": poll_error[3]}

    def _confirmed_read(fn):
        result = fn()
        st.session_state.pop("_dd_poll_error", None)
        return result

    limit = dd.get("limit_fraction")
    try:
        default_percent = float(limit) * 100 if limit is not None else 30.0
        if not math.isfinite(default_percent) or not 0 < default_percent < 100:
            default_percent = 30.0
    except (TypeError, ValueError):
        default_percent = 30.0
    st.number_input("계정 낙폭 한도 (%)", min_value=0.0, max_value=100.0,
                    value=default_percent, step=1.0, key="dd-limit")

    def _save_drawdown():
        percent = float(st.session_state["dd-limit"])
        if not math.isfinite(percent) or not 0 < percent < 100:
            raise ValueError("한도는 0% 초과 100% 미만이어야 한다")
        svc.save_drawdown_limit(percent / 100.0)

    st.button("낙폭 한도 저장", key="dd-save", on_click=trading.act(
        "낙폭 한도 저장", _save_drawdown, "낙폭 한도를 저장했다 — 감시 실행·계정 등록은 별도"))
    st.caption("기본 30%. 예: 80% → 0.80 저장. 한도를 올려도 기존 차단은 해제되지 않는다.")
    st.caption("이 설치의 등록 계정(DEMO·LIVE 포함)에 같은 한도를 적용한다. 계정별 관측 이력과 차단 상태는 분리한다.")

    def _dd_number(key, *, percent=False):
        value = dd.get(key)
        try:
            value = float(value)
            if math.isfinite(value):
                return f"{value:.2%}" if percent else f"{value:,.2f} USD"
        except (TypeError, ValueError):
            pass
        return "미관측"

    metrics = st.columns(4)
    for col, label, key, pct in zip(metrics,
            ("계정 equity", "관측 최고 equity", "현재 낙폭", "최대 관측 낙폭"),
            ("equity", "peak_equity", "drawdown", "max_drawdown"), (False, False, True, True)):
        col.metric(label, _dd_number(key, percent=pct))
    identity = dd.get("account_identity") or {}
    st.caption(f"계정: {identity} · epoch: {dd.get('epoch') or '미등록'}")
    observed = dd.get("observed_at")
    try:
        observed_text = datetime.fromtimestamp(float(observed), timezone.utc).isoformat() if observed is not None else "미관측"
    except (ValueError, TypeError, OverflowError, OSError):
        observed_text = "유효하지 않은 시각"
    st.caption(f"마지막 관측 (UTC): {observed_text} · 관측 공백(초): {dd.get('observation_gap_seconds')} "
               f"· 오래된 관측: {bool(dd.get('stale', True))}")
    if dd.get("blocked", True):
        st.error(f"신규 진입 차단 · latch={bool(dd.get('latched'))} · {dd.get('reason') or '계정 등록·유효 관측 필요'}")
    else:
        st.success("낙폭 검사상 신규 진입 허용 — 주문 직전 다시 검사한다")
    if not dd.get("enrolled"):
        st.info("계정 미등록 — 실제 UID와 유효한 equity 조회 후 명시적 초기화가 필요하다.")
    for error in dd.get("cancellation_errors") or []:
        st.error(f"신규 주문 취소 실패·잔여 주문 확인 필요: {error}")
    if dd.get("pending_orders"):
        st.warning("접수 불명 주문이 있다. 기록된 주문 ID를 거래소 기록과 대조해야 하며, 빈 조회만으로 차단을 풀 수 없다.")
        for pending in dd["pending_orders"]:
            st.caption(f"종목: {pending.get('symbol')} · 주문 ID: {pending.get('orderLinkId')}")
        st.code(f"python -m src.agent.drawdown_cli --mode {svc.mode} reconcile "
                "--order-link-id ORDER_LINK_ID --confirm", language="bash")
        st.caption("ORDER_LINK_ID에 위 기록된 ID를 넣는다. 대조 후에도 차단은 유지되며 별도 재개가 필요하다.")
    if dd.get("history"):
        with st.expander("이전 기준 epoch 이력"):
            st.json(dd.get("history"))
    st.caption("한도 도달(낙폭 ≥ 한도) 시 차단을 먼저 저장하고 식별된 에이전트 신규 진입 주문만 취소한다. "
               "보호 주문은 유지하며 자동 시장가 청산은 하지 않는다. 수동 킬스위치는 별도다.")
    has_keys = bool(svc.client.has_keys)
    def _poll_drawdown():
        observed = svc.poll_drawdown() or {}
        if observed.get("blocked", True):
            reason = observed.get("reason") or "유효 관측 없음 — 신규 진입 차단"
            st.session_state["_dd_poll_error"] = (svc.mode, observed.get("epoch"), observed.get("observed_at"), reason)
            raise RuntimeError(reason)
        st.session_state.pop("_dd_poll_error", None)
        return observed

    st.button("계정 낙폭 관측", key="dd-poll", disabled=not has_keys,
              on_click=trading.act("낙폭 관측", _poll_drawdown, "관측 요청 완료 — 차단 사유·시각을 확인한다"))
    init_ok = st.checkbox("계정 전체가 flat이고 현재 equity를 첫 기준으로 등록함을 확인한다", key="dd-init-ok")
    st.button("계정 낙폭 초기화", key="dd-init", disabled=not (has_keys and init_ok),
              on_click=trading.act("계정 낙폭 초기화", lambda: _confirmed_read(lambda: svc.initialize_drawdown(confirm=True)),
                                  "계정 낙폭 기준을 등록했다", reset=("dd-init-ok",)))
    st.text_input("기준금 재설정 사유 (입출금 조정 등)", key="dd-reason")
    rebase_ok = st.checkbox("운용을 멈췄고 계정 전체 flat·신규 미체결 없음·접수 불명 없음 확인", key="dd-rebase-ok")
    st.button("낙폭 기준금 재설정", key="dd-rebase",
              disabled=not (has_keys and rebase_ok and st.session_state.get("dd-reason", "").strip()),
              on_click=trading.act("낙폭 기준금 재설정", lambda: _confirmed_read(lambda: svc.reset_drawdown_baseline(
                  confirm=True, reason=st.session_state.get("dd-reason", ""))),
                  "새 기준 epoch를 저장했다 — 차단 유지, 별도 재개 필요", reset=("dd-rebase-ok",)))
    resume_ok = st.checkbox("최신 낙폭이 한도 미만임을 확인하고 MDD 차단 해제를 요청한다", key="dd-resume-ok")
    st.button("낙폭 보호 재개", key="dd-resume", disabled=not (has_keys and resume_ok),
              on_click=trading.act("낙폭 보호 재개", lambda: _confirmed_read(lambda: svc.resume_drawdown(confirm=True)),
                                  "MDD 차단을 해제했다 — 수동 HALT는 별도", reset=("dd-resume-ok",)))

c1, c2 = st.columns(2)
with c1, common.card("limits"):
    st.subheader("한도 설정")
    if lim is None:
        st.warning("한도가 없어 신규 주문이 막혀 있다. 아래 값을 직접 정한다(기본값 없음).")
    with st.form("limits-form"):
        lev = st.number_input("최대 레버리지 (배)", min_value=1.0, max_value=HARD_MAX_LEVERAGE,
                              value=lim.max_leverage if lim else None, step=1.0, key="l-lev")
        pos = st.number_input("최대 포지션 규모 (USDT, 명목가)", min_value=0.0,
                              value=lim.max_position_usdt if lim else None, step=100.0, key="l-pos")
        per = st.number_input("1회 최대 손실 (USDT, 손절 도달 시)", min_value=0.0,
                              value=lim.max_loss_per_trade_usdt if lim else None, step=10.0, key="l-per")
        day = st.number_input("일 최대 손실 (USDT, UTC 하루 실현+미실현)", min_value=0.0,
                              value=lim.max_daily_loss_usdt if lim else None, step=10.0, key="l-day")
        if st.form_submit_button("저장"):
            try:
                svc.save_limits(RiskLimits(max_leverage=float(lev or 0), max_position_usdt=float(pos or 0),
                                           max_loss_per_trade_usdt=float(per or 0), max_daily_loss_usdt=float(day or 0)))
                st.success("저장했다")
                lim = svc.limits()
            except ValueError as e:
                st.error(f"저장 안 됨 — {e}")

with c2, common.card("usage"):
    st.subheader("오늘 사용률")
    if trading.need_keys(svc):
        st.stop()
    with trading.guarded("손익 조회"):
        p = svc.position()
        pnl = svc.today_pnl(p)
        st.metric("오늘 손익 (UTC, 실현+미실현)", f"{pnl:,.2f} USDT")
        if lim:
            used = max(-pnl, 0) / lim.max_daily_loss_usdt
            st.progress(min(used, 1.0), text=f"일 손실 한도 사용 {used:.0%} / {lim.max_daily_loss_usdt:,.0f} USDT")
            notional = abs(svc.position_qty(p)) * float((p or {}).get("markPrice") or 0)
            st.progress(min(notional / lim.max_position_usdt, 1.0),
                        text=f"포지션 {notional:,.0f} / {lim.max_position_usdt:,.0f} USDT")
