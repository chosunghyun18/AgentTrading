"""analysis.aoa_study — E0 포지션 표: 경계·반대 넘김 분할·만기 정산·첫 진입 주문·평단·시각 규약·지갑·f0·u0.

기대값은 Obsidian design/aoa-skill-vs-luck.md "E0 공통 준비" 정의를 손으로 계산한 값.
"""

import io
import json
import zipfile

import numpy as np
import pandas as pd
import pytest

from src.analysis import aoa_study as A

T0 = pd.Timestamp("2020-01-02 10:00:30", tz="UTC")


def raw_row(execid, symbol, side, qty, px, cost, comm=0, liq="AddedLiquidity", exectype="Trade", orderid="o1",
            text="", t=T0):
    return {"execid": execid, "orderid": orderid, "symbol": symbol, "side": side, "lastqty": str(qty),
            "lastpx": str(px), "lastliquidityind": liq, "exectype": exectype, "execcost": str(cost),
            "execcomm": str(comm), "text": text, "transacttime": t.strftime("%Y-%m-%d %H:%M:%S.%f")}


def ex_of(rows):
    return A.prepare_executions(pd.DataFrame(rows, dtype=str))


def at(sec):
    return T0 + pd.Timedelta(seconds=sec)


def test_is_inverse():
    assert A.is_inverse("XBTUSD") and A.is_inverse("XBTZ18") and A.is_inverse("XBTH20")
    for s in ("ETHUSD", "XBT7D_U110", "XBTUSDT", "XRPU18", "LINKUSDT", "XBTUSDM21"):
        assert not A.is_inverse(s), s


def test_prepare_filters_types_maps_and_keeps_tie_order():
    rows = [raw_row("b", "XBTUSD", "Buy", 10, 100.0, 5, t=at(1), orderid="o2"),
            raw_row("a", "XBTUSD", "Sell", 10, 101.0, -4, liq="RemovedLiquidity", text="Liquidation", t=at(0)),
            raw_row("f", "XBTUSD", "", 0, "", 0, liq="", exectype="Funding", t=at(0)),
            raw_row("s", "XBTZ18", "", 7, 99.5, 3, liq="", exectype="Settlement", t=at(1)),
            raw_row("c", "XBTUSD", "Buy", 1, 100.0, 1, t=at(1), orderid="o3")]
    ex = A.prepare_executions(pd.DataFrame(rows, dtype=str))
    assert list(ex.order_id) == ["o1", "o2", "o1", "o3"]  # Funding 제외, 같은 시각은 원본 순서
    assert list(ex.sq) == [-10, 10, 0, 1]  # Settlement 은 0
    assert ex.liquidity.tolist()[:2] == ["taker", "maker"] and pd.isna(ex.liquidity.iloc[2])
    assert ex.is_liq.tolist() == [True, False, False, False] and ex.is_settle.tolist() == [False, False, True, False]
    assert str(ex.ts.dt.tz) == "UTC"


def test_prepare_rejects_bad_side():
    with pytest.raises(ValueError, match="side"):
        ex_of([raw_row("a", "XBTUSD", "", 1, 100.0, 1)])


def test_reconstruct_add_avgdown_and_close():
    # 롱 100(주문 A, 두 체결 60@100·40@102) → 주문 B 50@98 추가(평단 100.8 보다 낮음 = 물타기) → 150 청산.
    ex = ex_of([raw_row("1", "XBTUSD", "Buy", 60, 100.0, 600, comm=-15, orderid="A", t=at(0)),
                raw_row("2", "XBTUSD", "Buy", 40, 102.0, 400, comm=-10, liq="RemovedLiquidity", orderid="A",
                        t=at(10)),
                raw_row("3", "XBTUSD", "Buy", 50, 98.0, 500, comm=-12, orderid="B", t=at(60)),
                raw_row("4", "XBTUSD", "Sell", 150, 101.0, -1600, comm=30, orderid="C", t=at(600))])
    lg, pos, open_end = A.reconstruct(ex)
    assert open_end == {} and len(pos) == 1
    p = pos.iloc[0]
    assert p.side == 1 and p.t0 == at(0) and p.close_ts == at(600) and p.qmax == 150 and p.n_fills == 4
    assert p.gross_xbt == pytest.approx(100 / 1e8) and p.fee_xbt == pytest.approx(-(-15 - 10 - 12 + 30) / 1e8)
    assert p.net_xbt == pytest.approx(p.gross_xbt + p.fee_xbt)
    assert p.entry_order == "A" and p.entry_liq0 == "maker"
    assert p.q0 == 100 and p.p_vwap0 == pytest.approx((60 * 100 + 40 * 102) / 100)
    assert p.adds == 1 and bool(p.has_avgdown) is True
    assert p.hold_min == pytest.approx(10.0) and bool(p.inverse) is True
    assert lg.kind.tolist() == ["open", "add", "add", "reduce"]


def test_reconstruct_add_above_avg_is_not_avgdown():
    ex = ex_of([raw_row("1", "XBTUSD", "Sell", 10, 100.0, -10, orderid="A", t=at(0)),
                raw_row("2", "XBTUSD", "Sell", 10, 99.0, -10, orderid="B", t=at(5)),  # 숏 평단 100 보다 낮게 추가 = 불타기
                raw_row("3", "XBTUSD", "Buy", 20, 98.0, 25, orderid="C", t=at(9))])
    _, pos, _ = A.reconstruct(ex)
    assert pos.iloc[0].side == -1 and pos.iloc[0].adds == 1 and not pos.iloc[0].has_avgdown


def test_reconstruct_flip_splits_fill_proportionally():
    # +100 롱 뒤 한 체결로 300 매도 → rid0 청산 몫 1/3, rid1 숏 200 진입 몫 2/3. 비용·수수료 비례 배분.
    ex = ex_of([raw_row("1", "XBTUSD", "Buy", 100, 100.0, 1000, comm=10, orderid="A", t=at(0)),
                raw_row("2", "XBTUSD", "Sell", 300, 110.0, -2700, comm=30, orderid="F", liq="RemovedLiquidity",
                        t=at(120)),
                raw_row("3", "XBTUSD", "Buy", 200, 105.0, 1900, comm=20, orderid="G", t=at(300))])
    lg, pos, _ = A.reconstruct(ex)
    assert len(pos) == 2
    a, b = pos.iloc[0], pos.iloc[1]
    assert a.gross_xbt == pytest.approx(-(1000 - 900) / 1e8) and a.fee_xbt == pytest.approx(-(10 + 10) / 1e8)
    assert b.side == -1 and b.t0 == at(120) and b.q0 == 200 and b.p_vwap0 == 110.0
    assert b.entry_order == "F" and b.entry_liq0 == "taker"
    assert b.gross_xbt == pytest.approx(-(-1800 + 1900) / 1e8) and b.fee_xbt == pytest.approx(-(20 + 20) / 1e8)
    assert lg.frac.tolist() == pytest.approx([1, 1 / 3, 2 / 3, 1])


def test_reconstruct_settlement_closes_and_open_at_end_excluded():
    ex = ex_of([raw_row("1", "XBTZ18", "Sell", 50, 4000.0, -100, orderid="A", t=at(0)),
                raw_row("2", "XBTUSD", "Buy", 10, 100.0, 10, orderid="B", t=at(1)),
                raw_row("3", "XBTZ18", "", 50, 3900.0, 120, liq="", exectype="Settlement", orderid="", t=at(2)),
                raw_row("4", "XBTUSD", "Sell", 10, 101.0, -11, orderid="C", t=at(3)),
                raw_row("5", "XBTUSD", "Buy", 5, 100.0, 5, orderid="D", t=at(4))])
    lg, pos, open_end = A.reconstruct(ex)
    assert open_end == {"XBTUSD": 5}
    assert sorted(pos.symbol) == ["XBTUSD", "XBTZ18"]
    z = pos.set_index("symbol").loc["XBTZ18"]
    assert z.close_ts == at(2) and z.gross_xbt == pytest.approx(-20 / 1e8) and z.side == -1


def test_entry_order_only_counts_its_legs_in_same_position():
    # 주문 A 가 엶 → 주문 B 추가 → 주문 A 의 나머지 체결 추가(같은 주문 대기 지정가가 나중에 마저 체결).
    ex = ex_of([raw_row("1", "XBTUSD", "Buy", 30, 100.0, 1, orderid="A", t=at(0)),
                raw_row("2", "XBTUSD", "Buy", 20, 99.0, 1, orderid="B", t=at(30)),
                raw_row("3", "XBTUSD", "Buy", 10, 97.0, 1, orderid="A", t=at(90)),
                raw_row("4", "XBTUSD", "Sell", 60, 98.0, -3, orderid="C", t=at(200))])
    _, pos, _ = A.reconstruct(ex)
    p = pos.iloc[0]
    assert p.q0 == 40 and p.p_vwap0 == pytest.approx((30 * 100 + 10 * 97) / 40) and p.adds == 1


def test_price_at_uses_previous_bar_close():
    close = pd.Series([1.0, 2.0, 3.0], index=pd.date_range("2020-01-02 09:58", periods=3, freq="min", tz="UTC"))
    m = pd.Series(pd.to_datetime(["2020-01-02 10:00", "2020-01-02 09:58", "2020-01-02 10:05"], utc=True))
    got = A.price_at(m, close)
    assert got[0] == 2.0 and np.isnan(got[1]) and np.isnan(got[2])  # 10:00 의 가격 = 봉 09:59 close


def wallet_rows():
    # 1/1 입금 1 XBT, 1/2 손익 −0.1, 1/4 기록에 없는 0.5 유입 뒤 손익 +0.2(잔고 1.6, 누적 1.1 → 차 0.5).
    r = [("2020-01-01", 100_000_000, 100_000_000, "Deposit"), ("2020-01-01", 0, 0, ""),
         ("2020-01-02", -10_000_000, 90_000_000, "RealisedPNL"),
         ("2020-01-04", 20_000_000, "1.6E+08", "RealisedPNL")]
    return pd.DataFrame([{"date": d, "amount": str(a), "walletbalance": str(b), "transacttype": t} for d, a, b, t in r])


def test_wallet_end_balance_gap_and_ffill():
    bal = A.wallet_end_balance(wallet_rows(), end=pd.Timestamp("2020-01-05").date())
    assert list(bal.index.strftime("%d")) == ["01", "02", "03", "04", "05"]
    assert bal.bal_raw.tolist() == pytest.approx([1.0, 0.9, 0.9, 1.1, 1.1])
    assert bal.bal.tolist() == pytest.approx([1.0, 0.9, 0.9, 1.6, 1.6])
    e = A.prev_day_balance(pd.Series(pd.to_datetime(["2020-01-05 00:00:01", "2020-01-01 12:00:00"], utc=True)), bal)
    assert e.E_prev.iloc[0] == pytest.approx(1.6) and e.E_prev_raw.iloc[0] == pytest.approx(1.1)
    assert np.isnan(e.E_prev.iloc[1])


def test_position_table_f0_u0_inverse_only():
    t2 = pd.Timestamp("2020-01-03 10:00:30", tz="UTC")
    ex = ex_of([raw_row("1", "XBTUSD", "Buy", 1000, 10_000.0, 10_000_000, orderid="A", t=t2),
                raw_row("2", "XBTUSD", "Sell", 1000, 10_100.0, -10_100_000, orderid="B", t=t2 + pd.Timedelta(hours=1)),
                raw_row("3", "ETHUSD", "Buy", 10, 200.0, 5, orderid="C", t=t2),
                raw_row("4", "ETHUSD", "Sell", 10, 201.0, -6, orderid="D", t=t2 + pd.Timedelta(minutes=5))])
    _, pos, _ = A.reconstruct(ex)
    bal = A.wallet_end_balance(wallet_rows(), end=pd.Timestamp("2020-01-05").date())
    close = pd.Series([9_990.0], index=pd.DatetimeIndex([pd.Timestamp("2020-01-03 09:59", tz="UTC")]))
    tab = A.position_table(pos, bal, close).set_index("symbol")
    x = tab.loc["XBTUSD"]
    assert x.m0 == pd.Timestamp("2020-01-03 10:00", tz="UTC") and x.p_ref == 9_990.0
    assert x.E_prev == pytest.approx(0.9)
    assert x.f0 == pytest.approx((1000 / 10_000) / 0.9) and x.u0 == pytest.approx(x.net_xbt / 0.1)
    e = tab.loc["ETHUSD"]
    assert np.isnan(e.f0) and np.isnan(e.u0) and np.isnan(e.p_ref)


def test_leverage_by_year_and_topk():
    ex = ex_of([raw_row("1", "XBTUSD", "Buy", 1000, 10_000.0, 1, orderid="A", t=pd.Timestamp("2020-01-03 01:00", tz="UTC")),
                raw_row("2", "XBTUSD", "Buy", 1000, 5_000.0, 1, orderid="B", t=pd.Timestamp("2020-01-03 02:00", tz="UTC")),
                raw_row("3", "XBTUSD", "Sell", 2000, 5_000.0, 1, orderid="C", t=pd.Timestamp("2020-01-03 03:00", tz="UTC"))])
    lg, _, _ = A.reconstruct(ex)
    bal = A.wallet_end_balance(wallet_rows(), end=pd.Timestamp("2020-01-05").date())
    lev = A.leverage_by_year(lg, bal)
    assert lev.loc[2020] == pytest.approx((2000 / 5_000) / 0.9)  # 일 최대 명목 0.4 XBT / 전일 잔고 0.9
    net = pd.Series([5.0, -1.0, 2.0, -3.0])
    assert A.topk_share(net, 1) == pytest.approx((5 / 3, 5 / 11))


def make_zip(path, ex_rows, wallet):
    with zipfile.ZipFile(path, "w") as zf:
        buf = io.StringIO()
        pd.DataFrame(ex_rows).to_csv(buf, index=False)
        zf.writestr("aoa-execution-2020-01-01-2020-12-31.csv", buf.getvalue())
        buf = io.StringIO()
        wallet.to_csv(buf, index=False)
        zf.writestr("aoa-wallet-2020-01-01-2020-12-31.csv", buf.getvalue())


def test_run_e0_end_to_end(tmp_path, monkeypatch):
    t2 = pd.Timestamp("2020-01-03 10:00:30", tz="UTC")
    rows = [raw_row("1", "XBTUSD", "Buy", 1000, 10_000.0, 10_000_000, comm=-2500, orderid="A", t=t2),
            raw_row("2", "XBTUSD", "Sell", 1000, 10_100.0, -10_099_010, comm=7500, liq="RemovedLiquidity",
                    text="Liquidation", orderid="B", t=t2 + pd.Timedelta(hours=1))]
    z = tmp_path / "aoa.zip"
    make_zip(z, rows, wallet_rows())
    bars = pd.DataFrame({"ts": pd.date_range("2020-01-03 09:55", periods=10, freq="min", tz="UTC"),
                         "close": np.arange(10, dtype=float)})
    monkeypatch.setattr("src.ingest.store.load_bars", lambda *a, **k: bars)
    out = tmp_path / "out"
    s = A.run_e0(z, out)
    assert s["positions"] == 1 and s["liquidation_fills"] == 1 and s["win_rate"] == 1.0
    tab = pd.read_parquet(out / "positions.parquet")
    assert tab.p_ref.iloc[0] == 4.0  # 봉 09:59 close
    assert tab.net_xbt.iloc[0] == pytest.approx((10_099_010 - 10_000_000 - 5000) / 1e8)
    assert json.loads((out / "e0.json").read_text(encoding="utf-8"))["positions"] == 1


def test_main_missing_zip(tmp_path, capsys):
    assert A.main(["e0", "--zip", str(tmp_path / "none.zip")]) == 2
    assert "없음" in capsys.readouterr().err


def test_flip_order_later_fills_add_to_q0_and_settlement_at_flat_makes_no_leg():
    # 주문 F 가 +50 롱을 넘겨 숏 30 을 엶 → 같은 주문이 나중에 20 더 체결 → q0 = 50, t_q0_end = 둘째 체결.
    ex = ex_of([raw_row("1", "XBTUSD", "Buy", 50, 100.0, 1, orderid="A", t=at(0)),
                raw_row("2", "XBTUSD", "Sell", 80, 101.0, -1, orderid="F", t=at(10)),
                raw_row("3", "XBTUSD", "Sell", 20, 102.0, -1, orderid="F", t=at(70)),
                raw_row("4", "XBTUSD", "Buy", 50, 99.0, 1, orderid="G", t=at(100)),
                raw_row("5", "XBTZ18", "", 0, 4000.0, 0, liq="", exectype="Settlement", orderid="", t=at(200))])
    lg, pos, open_end = A.reconstruct(ex)
    assert open_end == {} and (lg.symbol == "XBTZ18").sum() == 0
    b = pos.iloc[1]
    assert b.entry_order == "F" and b.q0 == 50 and b.t_q0_end == at(70)
    assert b.p_vwap0 == pytest.approx((30 * 101 + 20 * 102) / 50) and b.adds == 0


def test_e_prev_unreliable_on_deposit_day_and_tiny_balance():
    bal = A.wallet_end_balance(wallet_rows(), end=pd.Timestamp("2020-01-05").date())
    ex = ex_of([raw_row("1", "XBTUSD", "Buy", 100, 10_000.0, 1, orderid="A", t=pd.Timestamp("2020-01-04 05:00", tz="UTC")),
                raw_row("2", "XBTUSD", "Sell", 100, 10_000.0, -1, orderid="B", t=pd.Timestamp("2020-01-04 06:00", tz="UTC")),
                raw_row("3", "XBTUSD", "Buy", 100, 10_000.0, 1, orderid="C", t=pd.Timestamp("2020-01-05 05:00", tz="UTC")),
                raw_row("4", "XBTUSD", "Sell", 100, 10_000.0, -1, orderid="D", t=pd.Timestamp("2020-01-05 06:00", tz="UTC"))])
    _, pos, _ = A.reconstruct(ex)
    w = wallet_rows()
    w.loc[len(w)] = {"date": "2020-01-04", "amount": "1", "walletbalance": "1", "transacttype": "Deposit"}
    tab = A.position_table(pos, bal, None, A.deposit_days(w))
    assert tab.e_prev_unreliable.tolist() == [True, False]  # 1/4 입금일, 1/5 는 전일 말 1.6 XBT
    tiny = bal.assign(bal=0.01)
    assert A.position_table(pos, tiny, None).e_prev_unreliable.tolist() == [True, True]


def test_price_at_rejects_naive_index():
    close = pd.Series([1.0], index=pd.DatetimeIndex([pd.Timestamp("2020-01-02 09:59")]))
    with pytest.raises(ValueError, match="UTC"):
        A.price_at(pd.Series(pd.to_datetime(["2020-01-02 10:00:00"], utc=True)), close)


def test_run_e0_rejects_out_of_sample(tmp_path, monkeypatch):
    rows = [raw_row("1", "XBTUSD", "Buy", 10, 100.0, 1, t=pd.Timestamp("2022-01-01 00:00:01", tz="UTC"))]
    z = tmp_path / "aoa.zip"
    make_zip(z, rows, wallet_rows())
    with pytest.raises(ValueError, match="표본"):
        A.run_e0(z, tmp_path / "out")
