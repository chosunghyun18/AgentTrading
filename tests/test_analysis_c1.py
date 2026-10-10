"""analysis.c1 — 지정가 통과 체결·미체결 취소·진입 봉 청산 순서·σ 배수 손절/익절·사이징·테이커 차이·누수 없음.

기대값은 Obsidian design/phase3-c1-meanrev-maker.md "체결 모델"·"손 계산 예" (a)~(g).
"""

import hashlib
import math

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from src.analysis import c1
from src.analysis.c1 import C1_GRID, C1Params, c1_position_size, generate_c1_run
from src.analysis.features import compute_features
from src.analysis.synthetic import MAX_EXPOSURE, Params, generate_run
from src.backtest.costs import apply_costs
from src.shared.schema import validate_roundtrips
from tests.test_analysis_synthetic import B, _bars, _mirror, _random_bars, ts

# n=3·k=1: 봉 0~4 워밍업, 봉 5 마감 z_3 = −1.13 → 롱 신호, 지정가 L = close[5] = B − 40.
WARM = [B, B + 10, B, B + 10, B]
SIG = (B, B, B - 40, B - 40)
T = 5
L = B - 40


def p(**kw):
    base = dict(n=3, k=1.0, exit="tp", stop_sigma=1.0)
    base.update(kw)
    return C1Params(**base)


def s_at(rows, t=T, stop_sigma=1.0, n=3):
    return stop_sigma * float(compute_features(_bars(rows), windows=(n,))[f"sigma_{n}"].iloc[t])


def levels(rows, entry, side="long", stop_sigma=1.0):
    return c1.exit_levels(side, entry, s_at(rows, stop_sigma=stop_sigma), True)


def run(rows, fill="maker", **kw):
    return generate_c1_run(_bars(rows), p(**kw), fill=fill)


FILL_BAR = (L + 5, L + 10, L - 1, L + 2)  # low < L → 통과 체결, 손절·익절 어느 쪽도 아님
FLAT = L + 2


# ---------------------------------------------------------------- 그리드·식별자

def test_grid_24_and_param_id():
    assert len(C1_GRID) == 24 and len({q.param_id for q in C1_GRID}) == 24
    assert C1Params(n=60, k=2.0, exit="mean", stop_sigma=1.0).param_id == "exit=mean;k=2;n=60;stop_sigma=1"
    assert {q.n for q in C1_GRID} == {60, 240} and {q.k for q in C1_GRID} == {2.0, 2.5}
    assert c1.strategy_id("maker") == "syn-c1-maker" and c1.strategy_id("taker") == "syn-c1-taker"
    with pytest.raises(ValueError):
        C1Params(n=60, k=2.0, exit="x", stop_sigma=1.0)
    with pytest.raises(ValueError):
        generate_c1_run(_bars(WARM), p(), fill="limit")


# ---------------------------------------------------------------- 메이커 진입

def test_maker_pass_fills_at_limit():
    rows = WARM + [SIG, FILL_BAR] + [FLAT] * 3
    res = run(rows)
    t = res.roundtrips.iloc[0]
    assert t["signal_ts"] == ts(T) and t["entry_ts"] == ts(T + 1) and t["entry_price"] == L
    assert t["side"] == "long" and t["entry_reason"] == "c1_meanrev_limit" and t["strategy_id"] == "syn-c1-maker"
    stop, tp = levels(rows, L)
    assert t["stop_price"] == stop and t["tp_price"] == tp
    assert t["exit_reason"] == "end_of_data" and res.unfilled_limit == 0


def test_maker_touch_only_is_unfilled():
    rows = WARM + [SIG, (L + 5, L + 10, L, L + 2)] + [FLAT] * 3
    res = run(rows)
    assert len(res.roundtrips) == 0 and res.unfilled_limit == 1


def test_cancel_bar_close_is_reevaluated():
    # n=4·k=0.8: 봉 6 마감 z_4 = −1.5 신호, 봉 7 은 L 에 딱 닿고(미체결) 마감 z_4 = −0.87 → 봉 7 마감 재평가로 새 지정가
    H, Lo = B + 100, B
    rows = [H] * 6 + [(H, H, Lo, Lo), (Lo + 5, Lo + 10, Lo, Lo), (Lo, Lo + 1, Lo - 5, Lo - 2), Lo - 2, Lo - 2]
    res = generate_c1_run(_bars(rows), C1Params(n=4, k=0.8, exit="time", stop_sigma=1.0))
    assert res.unfilled_limit == 1
    t = res.roundtrips.iloc[0]
    assert t["signal_ts"] == ts(7) and t["entry_ts"] == ts(8) and t["entry_price"] == Lo


def test_gap_open_beyond_limit_still_fills_at_limit_then_gap_stop():
    stop, _ = levels(WARM + [SIG], L)
    rows = WARM + [SIG, (stop - 10, stop - 5, stop - 20, stop - 10)] + [stop - 10] * 2
    t = run(rows).roundtrips.iloc[0]
    assert t["entry_price"] == L and t["exit_ts"] == ts(T + 1)
    assert t["exit_reason"] == "stop" and t["exit_price"] == stop - 10  # 진입 봉 갭 손절 = open


def test_maker_entry_bar_stop_touch():
    stop, _ = levels(WARM + [SIG], L)
    rows = WARM + [SIG, (L + 5, L + 10, stop - 1, stop + 1)] + [stop + 1] * 2
    t = run(rows).roundtrips.iloc[0]
    assert t["exit_reason"] == "stop" and t["exit_price"] == stop and t["holding_min"] == 0


def test_maker_entry_bar_skips_take_profit():
    _, tp = levels(WARM + [SIG], L)
    rows = WARM + [SIG, (L + 5, tp + 50, L - 1, tp + 40), (tp + 40, tp + 45, tp + 35, tp + 40)] + [tp + 40]
    t = run(rows).roundtrips.iloc[0]
    assert t["exit_reason"] == "take_profit" and t["exit_ts"] == ts(T + 2) and t["exit_price"] == tp


def test_take_profit_needs_pass_and_gap_gets_no_bonus():
    _, tp = levels(WARM + [SIG], L)
    touch = WARM + [SIG, FILL_BAR, (FLAT, tp, FLAT - 1, FLAT)] + [FLAT]
    assert run(touch).roundtrips.iloc[0]["exit_reason"] == "end_of_data"  # high == tp → 미체결
    gap = WARM + [SIG, FILL_BAR, (tp + 30, tp + 40, tp + 20, tp + 30)] + [tp + 30]
    t = run(gap).roundtrips.iloc[0]
    assert t["exit_reason"] == "take_profit" and t["exit_price"] == tp  # open 이 아니라 익절가


def test_stop_wins_when_both_hit():
    stop, tp = levels(WARM + [SIG], L)
    rows = WARM + [SIG, FILL_BAR, (FLAT, tp + 10, stop - 10, FLAT)] + [FLAT]
    t = run(rows).roundtrips.iloc[0]
    assert t["exit_reason"] == "stop" and t["exit_price"] == stop


@pytest.mark.parametrize("fill", ["maker", "taker"])
def test_short_mirror(fill):
    rows = _mirror(WARM + [SIG, FILL_BAR] + [FLAT] * 3)
    t = run(rows, fill=fill).roundtrips.iloc[0]
    assert t["side"] == "short" and t["signal_ts"] == ts(T)
    entry = 2 * B - L if fill == "maker" else rows[T + 1][0]
    assert t["entry_price"] == entry
    stop, tp = c1.exit_levels("short", entry, s_at(rows), True)
    assert t["stop_price"] == stop and t["tp_price"] == tp and stop > entry > tp


# ---------------------------------------------------------------- 청산 방식

def test_mean_revert_exit_next_open():
    rows = WARM + [SIG, FILL_BAR, (FLAT, FLAT + 1, FLAT - 1, FLAT), (FLAT + 3, FLAT + 4, FLAT, FLAT)] + [FLAT]
    t = run(rows, exit="mean").roundtrips.iloc[0]
    # 봉 6 마감 9962 < sma 9974 · 봉 7 마감 9962 ≥ sma 9961.3 → 봉 8 open 9965
    assert t["exit_reason"] == "mean_revert" and t["exit_signal_ts"] == ts(T + 2)
    assert t["exit_ts"] == ts(T + 3) and t["exit_price"] == FLAT + 3 and pd.isna(t["tp_price"])


def test_time_exit_after_n_minutes():
    rows = WARM + [SIG, FILL_BAR] + [FLAT] * 2 + [(FLAT + 1, FLAT + 2, FLAT, FLAT)] + [FLAT]
    t = run(rows, exit="time").roundtrips.iloc[0]
    assert t["exit_reason"] == "time" and t["exit_signal_ts"] == ts(T + 3)
    assert t["exit_ts"] == ts(T + 4) and t["exit_price"] == FLAT + 1 and t["holding_min"] == 3


def test_limit_filled_on_last_bar_closes_end_of_data():
    rows = WARM + [SIG, FILL_BAR]
    t = run(rows).roundtrips.iloc[0]
    assert t["exit_reason"] == "end_of_data" and t["holding_min"] == 0 and t["exit_price"] == FILL_BAR[3]
    assert t["exit_signal_ts"] == ts(T + 1)


def test_signal_on_last_bar_dropped():
    assert len(run(WARM + [SIG]).roundtrips) == 0


# ---------------------------------------------------------------- 테이커 모드

def test_taker_enters_at_next_open_and_takes_profit_on_entry_bar():
    o = L + 5
    s = s_at(WARM + [SIG])
    stop, tp = c1.exit_levels("long", o, s, True)
    rows = WARM + [SIG, (o, tp + 5, L, tp + 1)] + [FLAT]
    res = run(rows, fill="taker")
    t = res.roundtrips.iloc[0]
    assert t["entry_price"] == o and t["entry_reason"] == "c1_meanrev_market" and t["strategy_id"] == "syn-c1-taker"
    assert t["exit_reason"] == "take_profit" and t["exit_ts"] == ts(T + 1) and t["exit_price"] == tp
    assert res.unfilled_limit == 0
    touch = WARM + [SIG, (o, tp, L, tp)] + [FLAT]  # 진입 봉 high == tp → 미체결 (g)
    assert run(touch, fill="taker").roundtrips.iloc[0]["exit_reason"] == "end_of_data"


def test_taker_fills_every_signal_maker_may_not():
    rows = WARM + [SIG, (L + 5, L + 10, L, L + 2)] + [FLAT] * 3  # 딱 닿음: 메이커 미체결
    assert len(run(rows).roundtrips) == 0
    assert len(run(rows, fill="taker").roundtrips) == 1


# ---------------------------------------------------------------- 건너뜀·사이징

def test_wide_stop_skipped():
    res = run(WARM + [SIG, FILL_BAR, FLAT], stop_sigma=20.0)  # s ≈ 0.087 > S_MAX
    assert res.skipped_wide_stop == 1 and len(res.roundtrips) == 0 and res.unfilled_limit == 0


def test_min_qty_skipped():
    res = generate_c1_run(_bars(WARM + [SIG, FILL_BAR, FLAT]), p(), initial_equity=1e-9)
    assert res.skipped_min_qty == 1 and len(res.roundtrips) == 0


def test_sizing_hand_example_a():
    qty, capped = c1_position_size(1.0, 10_000.0, "long", 0.01)
    assert (qty, capped) == (9900, False)
    loss = qty * (1 / 9_900.0 - 1 / 10_000.0)
    assert loss == pytest.approx(0.0100, abs=1e-12)
    assert c1_position_size(1.0, 10_010.0, "long", 0.01)[0] == 9909  # (f) floor(9,909.9)
    q, cap = c1_position_size(1.0, 10_000.0, "short", 0.001)
    assert cap and q == math.floor(MAX_EXPOSURE * 10_000.0)


# ---------------------------------------------------------------- 비용(손 계산 예 c·c′·e·f)

def _rt(entry_reason, entry, exit_, reason, qty, side="long", strategy="syn-c1-maker"):
    pnl = qty * (1 / entry - 1 / exit_)
    t0 = pd.Timestamp("2020-01-01T00:00Z")
    df = pd.DataFrame([{
        "strategy_id": strategy, "param_id": "p", "trade_id": 0, "symbol": "XBTUSD", "side": side,
        "signal_ts": t0, "entry_ts": t0 + pd.Timedelta(minutes=1), "entry_price": entry,
        "exit_signal_ts": pd.Timestamp("NaT", tz="UTC"), "exit_ts": t0 + pd.Timedelta(minutes=30), "exit_price": exit_,
        "qty": qty, "notional_usd": float(qty), "leverage": qty / entry, "risk_pct": 1.0,
        "stop_price": entry * 0.99, "tp_price": np.nan, "exit_reason": reason, "entry_reason": entry_reason,
        "equity_before": 1.0, "size_capped": False, "gross_pnl_xbt": pnl, "gross_ret": pnl, "holding_min": 29.0}])
    from src.shared.schema import ROUNDTRIPS
    df["exit_signal_ts"] = pd.Series([pd.NaT], dtype="datetime64[ns, UTC]")
    return validate_roundtrips(df[ROUNDTRIPS.column_names].astype(ROUNDTRIPS.dtypes))


def test_costs_hand_examples():
    c = apply_costs(_rt("c1_meanrev_limit", 10_000.0, 9_900.0, "stop", 9900), "default").iloc[0]
    assert c["entry_liquidity"] == "maker" and c["entry_fill_price"] == 10_000.0
    assert c["exit_fill_price"] == pytest.approx(9_898.02)
    assert round(c["net_pnl_xbt"], 6) == -0.010798
    cp = _rt("c1_meanrev_limit", 10_000.0, 9_850.0, "stop", 9900).iloc[0]
    assert round(cp["gross_pnl_xbt"], 6) == -0.015076
    e = apply_costs(_rt("c1_meanrev_limit", 10_000.0, 10_100.0, "take_profit", 9900), "default").iloc[0]
    assert round(e["gross_pnl_xbt"], 6) == 0.009802 and round(e["fee_xbt"], 6) == 0.000394
    assert round(e["net_pnl_xbt"], 6) == 0.009408
    f = apply_costs(_rt("c1_meanrev_market", 10_010.0, 10_110.1, "take_profit", 9909,
                        strategy="syn-c1-taker"), "default").iloc[0]
    assert f["entry_liquidity"] == "taker" and f["entry_fill_price"] == pytest.approx(10_012.002)
    assert round(f["gross_pnl_xbt"], 6) == 0.009801 and round(f["fee_xbt"], 6) == 0.000592
    assert round(f["net_pnl_xbt"], 6) == 0.009011


V1_GOLDEN = {"default": "9d9a1eece897f03946eab01ae89cc138d2c6a43b7805c99adbf91c273a49c22d",
             "bybit": "c1ea3db4d5af7c80a2df522f27e57623a56fe48dc5ad84cb7064ec7053eafb49"}


@pytest.mark.parametrize("profile", sorted(V1_GOLDEN))
def test_v1_costs_bytes_unchanged(profile):
    """MAKER_ENTRY_REASONS 도입 전(2026-10-10) 해시와 같다 — v1 행 결과 바이트 불변."""
    bars = _random_bars(n=6000, seed=7)
    rt = pd.concat([generate_run(bars, Params(trigger=t, n=15, k=None if t == "h1" else 1.5, stop_pct=0.5,
                                              tp_r=tp, max_hold=60, risk_pct=1)).roundtrips
                    for t in ("h1", "h2", "h3") for tp in (1.0, None)], ignore_index=True)
    net = apply_costs(rt, profile)
    h = hashlib.sha256(pd.util.hash_pandas_object(net, index=True).values.tobytes()
                       + str(list(net.dtypes)).encode()).hexdigest()
    assert h == V1_GOLDEN[profile]


# ---------------------------------------------------------------- 임의 봉: 불변식·누수 없음·결정성

RAND = dict(n=15, k=1.5)


@pytest.mark.parametrize("fill", ["maker", "taker"])
@pytest.mark.parametrize("exit_", ["mean", "tp", "time"])
def test_random_bars_invariants(fill, exit_):
    bars = _random_bars(n=4000, seed=3)
    res = generate_c1_run(bars, C1Params(exit=exit_, stop_sigma=2.0, **RAND), fill=fill)
    rt = res.roundtrips
    assert len(rt) > 20
    assert (rt["entry_ts"] - rt["signal_ts"] == pd.Timedelta(minutes=1)).all()
    assert (rt["entry_ts"].iloc[1:].to_numpy() >= rt["exit_ts"].iloc[:-1].to_numpy()).all()
    assert (rt["leverage"] <= MAX_EXPOSURE + 1e-12).all()
    assert set(rt["exit_reason"]) <= {"stop", "take_profit", "time", "mean_revert", "end_of_data"}
    close = bars.set_index("ts")["close"]
    if fill == "maker":
        assert (rt["entry_price"].to_numpy() == close.loc[rt["signal_ts"]].to_numpy()).all()
        assert res.unfilled_limit > 0
    else:
        assert res.unfilled_limit == 0
    long = rt["side"] == "long"
    assert (rt.loc[long, "stop_price"] < rt.loc[long, "entry_price"]).all()
    assert (rt.loc[~long, "stop_price"] > rt.loc[~long, "entry_price"]).all()
    again = generate_c1_run(bars, C1Params(exit=exit_, stop_sigma=2.0, **RAND), fill=fill)
    pdt.assert_frame_equal(rt, again.roundtrips)


@pytest.mark.parametrize("fill", ["maker", "taker"])
def test_prefix_invariance_no_lookahead(fill):
    bars = _random_bars(n=3000, seed=5)
    q = C1Params(exit="tp", stop_sigma=2.0, **RAND)
    full = generate_c1_run(bars, q, fill=fill).roundtrips
    for m in (700, 1500, 2400):
        part = generate_c1_run(bars.iloc[:m].copy(), q, fill=fill).roundtrips
        closed = part[part["exit_reason"] != "end_of_data"]
        assert len(closed) > 0
        pdt.assert_frame_equal(closed.reset_index(drop=True), full.iloc[:len(closed)].reset_index(drop=True))


def test_future_bars_do_not_change_signal_or_limit():
    rows = WARM + [SIG, FILL_BAR] + [FLAT] * 3
    a = run(rows).roundtrips.iloc[0]
    rows2 = WARM + [SIG, FILL_BAR, (FLAT, FLAT + 900, FLAT - 900, FLAT - 500)] + [FLAT - 500]
    b = run(rows2).roundtrips.iloc[0]
    for col in ("signal_ts", "entry_ts", "entry_price", "qty", "stop_price", "tp_price"):
        assert a[col] == b[col]
