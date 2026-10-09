"""src/ingest/aoa.py — aoa 원본 zip → fills(source=aoa) 일별 parquet, src/ingest/store.load_fills."""

from __future__ import annotations

import json
import logging
import zipfile
from datetime import date

import pandas as pd
import pytest

from src.ingest import aoa, store
from src.shared.schema import FILLS, SchemaError

HEADER = ("date,execid,orderid,clordid,clordlinkid,account,symbol,side,lastqty,lastpx,lastliquidityind,orderqty,"
          "price,displayqty,stoppx,pegoffsetvalue,pegpricetype,currency,settlcurrency,exectype,ordtype,timeinforce,"
          "execinst,contingencytype,ordstatus,triggered,workingindicator,ordrejreason,leavesqty,cumqty,avgpx,"
          "commission,tradepublishindicator,text,trdmatchid,execcost,execcomm,homenotional,foreignnotional,"
          "transacttime,timestamp")


def row(execid, symbol, side, qty, px, liq, exectype, ordtype, comm, t, orderid="o1", match="m", text="x"):
    vals = {"date": t[:10], "execid": execid, "orderid": orderid, "account": "aoa", "symbol": symbol, "side": side,
            "lastqty": qty, "lastpx": px, "lastliquidityind": liq, "currency": "USD", "settlcurrency": "XBt",
            "exectype": exectype, "ordtype": ordtype, "text": text, "trdmatchid": match, "execcomm": comm,
            "transacttime": t, "timestamp": t}
    return ",".join(str(vals.get(c, "")) for c in HEADER.split(","))


FILE_A = [  # 2018 파일: 자정 경계 + Funding
    row("e1", "XBTUSD", "Sell", 2000, 11441.5, "RemovedLiquidity", "Trade", "Market", 11799,
        "2018-03-05 23:59:59.999999", orderid="o1", match="m1"),
    row("e2", "XBTUSD", "Buy", 100, 11000.0, "AddedLiquidity", "Trade", "Limit", -6,
        "2018-03-06 00:00:00.000001", orderid="o2", match="m2"),
    row("f1", "XBTUSD", "", 5000, 11000.0, "", "Funding", "Limit", 300, "2018-03-06 04:00:00.0", orderid=""),
]
FILE_B = [  # 2019 파일: 메이커 ETHUSD, Settlement, 같은 시각 2건(원본 순서 유지)
    row("e3", "ETHUSD", "Buy", 7, 200.5, "AddedLiquidity", "Trade", "Limit", -1,
        "2019-01-01 08:40:18.813286", orderid="o3", match="m3"),
    row("e4", "ETHUSD", "Sell", 3, 200.5, "AddedLiquidity", "Trade", "Limit", -2,
        "2019-01-01 08:40:18.813286", orderid="o3", match=""),
    row("s1", "XBTM19", "Buy", 1, 11918.3, "", "Settlement", "Limit", 0, "2019-06-28 11:59:59.999999", orderid=""),
]


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("90일 서한.txt", "letter")
        for name, rows in files.items():
            zf.writestr(name, "﻿" + HEADER + "\n" + "\n".join(rows) + "\n")
    return path


@pytest.fixture
def zpath(tmp_path):
    return make_zip(tmp_path / "aoa.zip", {"aoa-execution-2018.csv": FILE_A, "aoa-execution-2019.csv": FILE_B})


def test_to_fills_mapping(zpath):
    f = aoa.to_fills(aoa.read_executions(zpath))
    assert list(f.columns) == FILLS.column_names
    assert f["source_id"].tolist() == ["e1", "e2", "e3", "e4"]  # Trade 만, ts 정렬, 같은 시각은 원본 순서
    first = f.iloc[0]
    assert first["ts"] == pd.Timestamp("2018-03-05 23:59:59.999999", tz="UTC")
    assert (first["side"], first["qty"], first["price"]) == ("sell", 2000, 11441.5)
    assert first["fee"] == pytest.approx(11799 / 1e8) and first["fee_currency"] == "XBT"
    assert (first["liquidity"], first["ord_type"], first["order_id"], first["trd_match_id"]) == \
        ("taker", "Market", "o1", "m1")
    assert f.loc[1, "liquidity"] == "maker" and f.loc[1, "fee"] < 0  # 리베이트 음수 유지
    assert f["source"].eq("aoa").all() and f["leverage"].isna().all() and f["strategy_id"].isna().all()
    assert pd.isna(f.loc[3, "trd_match_id"])  # 빈 문자열 → null


@pytest.mark.parametrize("col,val,msg", [
    ("lastqty", "1.5", "정수가 아닌"),
    ("lastliquidityind", "Weird", "알 수 없는"),
    ("settlcurrency", "USDt", "XBt"),
])
def test_to_fills_rejects_bad_values(zpath, col, val, msg):
    raw = aoa.read_executions(zpath)
    raw.loc[raw["exectype"] == "Trade", col] = val
    with pytest.raises(ValueError, match=msg):
        aoa.to_fills(raw)


def test_to_fills_rejects_duplicate_execid_across_files(tmp_path):
    z = make_zip(tmp_path / "dup.zip", {"aoa-execution-a.csv": FILE_A[:1], "aoa-execution-b.csv": FILE_A[:1]})
    with pytest.raises(SchemaError, match="중복"):
        aoa.to_fills(aoa.read_executions(z))


def test_run_writes_daily_files_and_manifest(zpath, tmp_path):
    out = tmp_path / "out"
    m = aoa.run(zpath, out)
    assert m["skipped"] is False and m["rows"] == 4
    assert m["days"] == {"20180305": 1, "20180306": 1, "20190101": 2}  # UTC 자정 경계로 분할
    assert m["zip_sha256"] == aoa.sha256_of(zpath) and m["schema"] == aoa.schema_fingerprint()
    day = pd.read_parquet(aoa.fills_path(out, "20190101"))
    assert day["source_id"].tolist() == ["e3", "e4"]
    assert json.loads(aoa.manifest_path(out).read_text())["days"] == m["days"]


def test_run_skips_when_current_and_rebuilds_on_change(zpath, tmp_path, monkeypatch):
    out = tmp_path / "out"
    aoa.run(zpath, out)
    assert aoa.run(zpath, out)["skipped"] is True
    aoa.fills_path(out, "20180305").unlink()  # 일 파일 사라짐 → 재생성
    assert aoa.run(zpath, out)["skipped"] is False
    monkeypatch.setattr(aoa, "schema_fingerprint", lambda: "changed")  # 스키마 변경 → 재생성
    assert aoa.run(zpath, out)["skipped"] is False


def test_run_removes_stale_day_files(zpath, tmp_path):
    out = tmp_path / "out"
    aoa.run(zpath, out)
    z2 = make_zip(tmp_path / "v2.zip", {"aoa-execution-2018.csv": FILE_A})  # 2019 파일 없는 새 원본
    m = aoa.run(z2, out)
    assert set(m["days"]) == {"20180305", "20180306"}
    assert not aoa.fills_path(out, "20190101").exists()


def test_load_fills(zpath, tmp_path, caplog):
    out = tmp_path / "out"
    aoa.run(zpath, out)
    f = store.load_fills(date(2018, 3, 1), date(2018, 12, 31), out_dir=out)
    assert f["source_id"].tolist() == ["e1", "e2"]  # 거래 없는 날은 오류 없이 건너뜀
    with caplog.at_level(logging.WARNING):
        store.load_fills(date(2018, 3, 1), date(2020, 1, 1), out_dir=out)
    assert "일 범위" in caplog.text
    assert len(store.load_fills(date(2018, 4, 1), date(2018, 4, 2), out_dir=out)) == 0


def test_load_fills_requires_manifest(tmp_path):
    with pytest.raises(FileNotFoundError, match="정규화"):
        store.load_fills(date(2018, 3, 1), date(2018, 3, 2), out_dir=tmp_path)
    with pytest.raises(ValueError, match="source"):
        store.load_fills(date(2018, 3, 1), date(2018, 3, 2), source="synthetic")


def test_main_missing_zip(tmp_path):
    assert aoa.main(["--zip", str(tmp_path / "none.zip"), "--out", str(tmp_path / "o")]) == 2
