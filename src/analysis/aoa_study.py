"""aoa 실력·운 판별 실험 E0 — 원본 zip 체결·지갑 → 포지션 표(진입 시점 크기·결과·기준가).

입력: 사람이 받아 둔 `data/raw/aoa_public_2021-12-31_with_letter.zip`(진위 확인, 개인 비공개 연구 전용)과
XBTUSD 1분봉(`store.load_bars`, 기본 표본 [2018-03-01, 2022-01-01)).
출력: `position_table` 의 포지션 표와 `e0_summary` 의 재현·추가 보고 수치. CLI 는 `data/out/research/aoa_skill/`(git 제외).
설계 근거(단일 기준): Obsidian `Projects/work/AgentTrading/design/aoa-skill-vs-luck.md` "E0 공통 준비",
정의 원본 `research/aoa-behavior-metrics.md`(T-20261009-13). 문서와 이 파일이 다르면 문서를 따른다.

    python -m src.analysis.aoa_study e0          # → positions.parquet · e0.json

| 열 | 정의 |
|---|---|
| 포지션(`rid`) | 심볼별 부호 수량 누적 0 → ≠0 → 0. 반대로 넘기는 체결은 0 에서 끊어 두 다리로 나눈다 |
| `gross_xbt`·`fee_xbt`·`net_xbt` | −Σ`execcost`/1e8 · −Σ`execcomm`/1e8 · 합. 나뉜 체결은 수량 비례 배분. Settlement 은 남은 포지션을 닫는다 |
| `t0`·`m0` | 첫 진입 다리 시각 · 그 분 시작(`floor(t0, 1m)`) |
| `entry_liq0`·`q0`·`p_vwap0` | 포지션을 연 주문의 첫 다리 유동성 · 그 주문이 이 포지션에 더한 수량 합 · 그 다리들의 VWAP |
| `p_ref` | 시각 m0 의 가격 = 봉 `ts = m0 − 1m` 의 close(봉 T 의 close 는 시각 T+1m 의 가격). XBTUSD 만 |
| `qmax`·`adds`·`has_avgdown` | 최대 |포지션| · 첫 진입 주문 뒤 같은 방향으로 늘린 주문 수 · 그 주문 VWAP 가 직전 평단보다 불리한 적 있음 |
| `E_prev`·`E_prev_raw` | t0 UTC 전일 말 지갑 잔고(XBT) — 보정(일별 잔고−누적 금액 차의 최빈값을 더함) · 보정 전(누적 금액) |
| `f0`·`u0` | 인버스만: `(q0/p_vwap0)/E_prev` · `net_xbt/(q0/p_vwap0)`. 그 밖 심볼은 NaN |
| `t_q0_end` | 진입 주문이 이 포지션에 더한 마지막 다리 시각 — 대기 지정가면 t0 이후에야 q0 가 정해진다(사후 진단용) |
| `e_prev_unreliable` | t0 UTC 당일 Deposit 기록이 있거나 `E_prev` < 0.05 XBT — 전일 말 잔고가 결정 시점 자본을 대표하지 못함 |

- 인버스 = `XBTUSD` + XBT 만기물(`XBT` + 월 코드 + 연 2자리). `qty/price` 가 XBT 명목인 계약만이다.
- 평단(`has_avgdown` 용)은 인버스·선형 구분 없이 가격 가중 평균이다(T-13 비교용 정의 그대로).
- 손익에 펀딩(Funding 행)은 넣지 않는다(T-07 정의). 계좌 수준(E3)은 지갑 잔고를 따로 쓴다.
- 난수 없음. 같은 입력이면 같은 결과. 같은 시각 체결은 원본 파일·행 순서를 지킨다(stable 정렬).
  이 순서가 실제 매칭 순서라는 보장은 없다 — 뒤집어 재구성하면 경계는 같고 net 이 바뀌는 포지션 718개(합 1.9 XBT,
  최대 0.49, 승률 75.57 → 75.63%). 반대 넘김 체결에서 어느 가격이 청산 몫인지가 순서에 달려 있어서다.
- 체결 시각은 기본 표본 [2018-03-01, 2022-01-01) 안이어야 한다(`run_e0` 가 밖이면 ValueError).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import zipfile
from datetime import date
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from src.ingest.aoa import DEFAULT_ZIP, EXEC_PREFIX, LIQUIDITY

log = logging.getLogger(__name__)

SAMPLE_START = date(2018, 3, 1)
SAMPLE_END = date(2021, 12, 31)  # 포함 — 기본 표본 [2018-03-01, 2022-01-01)
OUT_DIR = Path("data/out/research/aoa_skill")
WALLET_PREFIX = "aoa-wallet-"
EXEC_COLUMNS = ("execid", "orderid", "symbol", "side", "lastqty", "lastpx", "lastliquidityind", "exectype",
                "execcost", "execcomm", "text", "transacttime")
WALLET_COLUMNS = ("date", "amount", "walletbalance", "transacttype")
INVERSE_RE = re.compile(r"XBT(USD|[FGHJKMNQUVXZ]\d\d)")
ONE_MIN = pd.Timedelta(minutes=1)
SAT = 1e8
MIN_E_PREV = 0.05  # XBT — 이보다 작은 전일 말 잔고는 f0 분모로 믿지 않는다


def is_inverse(symbol: str) -> bool:
    return INVERSE_RE.fullmatch(symbol) is not None


# --- 읽기 ---------------------------------------------------------------------------------------------------------

def read_raw(zip_path: Path = DEFAULT_ZIP) -> tuple[pd.DataFrame, pd.DataFrame]:
    """zip 의 실행 CSV(파일명 순)·지갑 CSV 를 문자열 열로 읽는다. 필요한 열만."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        ex_names = sorted(n for n in names if Path(n).name.startswith(EXEC_PREFIX) and n.endswith(".csv"))
        w_names = [n for n in names if Path(n).name.startswith(WALLET_PREFIX) and n.endswith(".csv")]
        if not ex_names or len(w_names) != 1:
            raise ValueError(f"{zip_path}: 실행 CSV {len(ex_names)}개·지갑 CSV {len(w_names)}개 — 각각 ≥1·1개여야 한다")
        kw = dict(encoding="utf-8-sig", dtype=str, keep_default_na=False)
        frames = []
        for n in ex_names:
            with zf.open(n) as f:
                frames.append(pd.read_csv(f, usecols=list(EXEC_COLUMNS), **kw))
        with zf.open(w_names[0]) as f:
            wallet = pd.read_csv(f, usecols=list(WALLET_COLUMNS), **kw)
    return pd.concat(frames, ignore_index=True), wallet


def prepare_executions(raw: pd.DataFrame) -> pd.DataFrame:
    """원본 실행 행(문자열) → Trade·Settlement 만, 타입 변환, `ts` stable 정렬.

    `sq` = 부호 수량(Settlement 은 0 — 재구성에서 남은 포지션을 닫는다). `cost`·`comm` 은 XBt 정수.
    """
    x = raw[raw["exectype"].isin(["Trade", "Settlement"])]
    settle = (x["exectype"] == "Settlement").to_numpy()
    bad = set(x.loc[~settle, "side"]) - {"Buy", "Sell"}
    if bad:
        raise ValueError(f"Trade 행의 알 수 없는 side: {sorted(bad)}")
    sign = np.where(x["side"] == "Buy", 1, -1)
    qty = x["lastqty"].astype("int64").to_numpy()
    out = pd.DataFrame({
        "ts": pd.to_datetime(x["transacttime"], utc=True, format="mixed").dt.as_unit("ns").to_numpy(),
        "symbol": x["symbol"].to_numpy(),
        "order_id": x["orderid"].to_numpy(),
        "qty": qty,
        "sq": np.where(settle, 0, sign * qty),
        "price": x["lastpx"].replace("", "nan").astype("float64").to_numpy(),
        "cost": x["execcost"].replace("", "0").astype("int64").to_numpy(),
        "comm": x["execcomm"].replace("", "0").astype("int64").to_numpy(),
        "liquidity": x["lastliquidityind"].map(LIQUIDITY).to_numpy(),
        "is_settle": settle,
        "is_liq": (x["text"] == "Liquidation").to_numpy(),
    })
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    return out.sort_values("ts", kind="stable", ignore_index=True)


# --- 재구성 ---------------------------------------------------------------------------------------------------------

def reconstruct(ex: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """체결 → 다리(legs)·포지션(positions)·끝에 열린 포지션.

    다리 = 한 체결이 한 포지션에 준 몫. 반대로 넘기는 체결은 (청산, 새 진입) 두 다리, 몫 `frac` 은 수량 비례.
    `kind`: open(0 에서 엶)·add(같은 방향으로 늘림)·reduce(줄임). `avg_before` 는 다리 직전 평단(가격 가중).
    """
    legs: list[tuple] = []
    state: dict[str, tuple[int, float, int]] = {}  # symbol → (포지션, 평단, rid)
    next_rid = 0
    cols = zip(range(len(ex)), ex["symbol"], ex["sq"], ex["price"], ex["is_settle"])
    for i, sym, sq, px, st in cols:
        p, avg, rid = state.get(sym, (0, np.nan, -1))
        if st:
            sq = -p
        total = abs(sq)
        rem = sq
        while rem != 0:
            if p == 0:
                rid = next_rid
                next_rid += 1
                kind, avg_b = "open", np.nan
                part = rem
            elif np.sign(rem) == np.sign(p):
                kind, avg_b, part = "add", avg, rem
            else:
                kind, avg_b = "reduce", avg
                part = -p if abs(rem) > abs(p) else rem
            if kind != "reduce":
                avg = px if p == 0 else (avg * abs(p) + px * abs(part)) / (abs(p) + abs(part))
            p += part
            rem -= part
            legs.append((i, rid, kind, part, abs(part) / total, p, avg_b))
            if p == 0:
                avg = np.nan
        state[sym] = (p, avg, rid)
    open_at_end = {s: int(v[0]) for s, v in state.items() if v[0] != 0}

    lg = pd.DataFrame(legs, columns=["fill", "rid", "kind", "part", "frac", "pos_after", "avg_before"])
    f = ex.iloc[lg["fill"].to_numpy()].reset_index(drop=True)
    lg = pd.concat([lg, f[["ts", "symbol", "order_id", "price", "liquidity", "is_settle", "is_liq"]]], axis=1)
    lg["cost"] = f["cost"].to_numpy() * lg["frac"].to_numpy()
    lg["comm"] = f["comm"].to_numpy() * lg["frac"].to_numpy()
    lg["abs_part"] = lg["part"].abs()
    lg = lg[~lg["rid"].isin(_open_rids(lg, open_at_end))].reset_index(drop=True)

    g = lg.groupby("rid", sort=True)
    pos = pd.DataFrame({
        "symbol": g["symbol"].first(),
        "side": np.sign(g["part"].first()).astype("int64"),
        "t0": g["ts"].first(),
        "close_ts": g["ts"].last(),
        "n_fills": g.size(),
        "qmax": g["pos_after"].agg(lambda s: s.abs().max()),
        "gross_xbt": -g["cost"].sum() / SAT,
        "fee_xbt": -g["comm"].sum() / SAT,
        "n_liq": g["is_liq"].sum().astype("int64"),
    })
    pos["net_xbt"] = pos["gross_xbt"] + pos["fee_xbt"]
    pos = pos.join(_entry_order(lg)).join(_adds(lg))
    pos["hold_min"] = (pos["close_ts"] - pos["t0"]).dt.total_seconds() / 60
    pos["inverse"] = pos["symbol"].map(is_inverse)
    return lg, pos.reset_index(), open_at_end


def _open_rids(lg: pd.DataFrame, open_at_end: dict[str, int]) -> set[int]:
    """표본 끝에 닫히지 않은 포지션 — 각 심볼의 마지막 rid."""
    return {int(lg.loc[lg["symbol"] == s, "rid"].iloc[-1]) for s in open_at_end}


def _entry_order(lg: pd.DataFrame) -> pd.DataFrame:
    """포지션을 연 주문이 그 포지션에 더한 다리 → entry_liq0·q0·p_vwap0."""
    first = lg[lg["kind"] == "open"].set_index("rid")
    ent = lg[lg["kind"] != "reduce"].copy()
    ent = ent[ent["order_id"].to_numpy() == first["order_id"].reindex(ent["rid"]).to_numpy()]
    ent["pq"] = ent["price"] * ent["abs_part"]
    g = ent.groupby("rid")
    out = pd.DataFrame({"entry_order": first["order_id"], "entry_liq0": first["liquidity"],
                        "q0": g["abs_part"].sum(), "t_q0_end": g["ts"].last()})
    out["p_vwap0"] = g["pq"].sum() / out["q0"]
    return out


def _adds(lg: pd.DataFrame) -> pd.DataFrame:
    """첫 진입 주문 뒤 같은 방향 추가 주문 수·물타기(주문 VWAP 가 첫 추가 다리 직전 평단보다 불리) 여부."""
    first_order = lg[lg["kind"] == "open"].set_index("rid")["order_id"]
    a = lg[(lg["kind"] == "add") & (lg["order_id"].to_numpy() != first_order.reindex(lg["rid"]).to_numpy())].copy()
    a["pq"] = a["price"] * a["abs_part"]
    a["dir"] = np.sign(a["part"])
    o = a.groupby(["rid", "order_id"], sort=False).agg(pq=("pq", "sum"), q=("abs_part", "sum"),
                                                        avg0=("avg_before", "first"), dir=("dir", "first"))
    vwap = o["pq"] / o["q"]
    o["worse"] = np.where(o["dir"] > 0, vwap < o["avg0"], vwap > o["avg0"])
    r = o.groupby(level="rid").agg(adds=("q", "size"), has_avgdown=("worse", "any")).reindex(lg["rid"].unique())
    return pd.DataFrame({"adds": r["adds"].fillna(0).astype("int64"), "has_avgdown": r["has_avgdown"].eq(True)})


# --- 지갑 --------------------------------------------------------------------------------------------------------------

def wallet_end_balance(wallet: pd.DataFrame, end: date = SAMPLE_END) -> pd.DataFrame:
    """UTC 일말 지갑 잔고(XBT). `bal_raw` = 기록된 금액 누적, `bal` = 그 날 (잔고 − 누적) 차의 최빈값을 더한 보정 판.

    차는 기록에 없는 유입(최종 17.99 XBT) 때문에 생긴다. 거래 없는 날은 전일 값을 이어 쓴다.
    """
    w = wallet[wallet["transacttype"] != ""].copy()
    w["amt"] = w["amount"].astype("float64") / SAT  # 일부 행이 지수 표기(1.00334E+11)라 실수로 읽는다
    w["bal"] = w["walletbalance"].astype("float64") / SAT
    w["date"] = pd.to_datetime(w["date"])
    w = w.sort_values("date", kind="stable")
    w["cum"] = w["amt"].cumsum()
    gap = (w["bal"] - w["cum"]).round(2).groupby(w["date"]).agg(lambda s: s.mode().iloc[0])
    raw = w.groupby("date")["cum"].last()
    days = pd.date_range(raw.index.min(), pd.Timestamp(end))
    return pd.DataFrame({"bal_raw": raw, "bal": raw + gap}).reindex(days).ffill()


def deposit_days(wallet: pd.DataFrame) -> set[pd.Timestamp]:
    """Deposit 기록이 있는 UTC 날짜. 지갑 date 는 일 단위라 당일 입금이 진입 전인지 후인지는 모른다."""
    return set(pd.to_datetime(wallet.loc[wallet["transacttype"] == "Deposit", "date"]))


def prev_day_balance(t: pd.Series, bal: pd.DataFrame) -> pd.DataFrame:
    """시각 t 의 UTC 전일 말 잔고(`E_prev`·`E_prev_raw`). 지갑 첫날 이전이면 NaN."""
    day = t.dt.tz_convert(None).dt.normalize() - pd.Timedelta(days=1)
    b = bal.reindex(day.to_numpy())
    return pd.DataFrame({"E_prev": b["bal"].to_numpy(), "E_prev_raw": b["bal_raw"].to_numpy()}, index=t.index)


# --- 포지션 표 ------------------------------------------------------------------------------------------------------

def price_at(minutes: pd.Series, close: pd.Series) -> np.ndarray:
    """시각 X(분 경계)의 가격 = 봉 `ts = X − 1m` 의 close. 봉이 없으면 NaN."""
    if str(getattr(close.index, "tz", None)) != "UTC":
        raise ValueError(f"1분봉 인덱스는 UTC 여야 한다(지금 {getattr(close.index, 'tz', None)}) — 아니면 조용히 전부 NaN")
    return close.reindex((minutes - ONE_MIN).to_numpy()).to_numpy()


def position_table(pos: pd.DataFrame, bal: pd.DataFrame, close: pd.Series | None,
                   deposits: set[pd.Timestamp] = frozenset()) -> pd.DataFrame:
    """`reconstruct` 의 포지션 + 지갑 잔고 + XBTUSD 1분봉 close(`ts` 인덱스) + 입금일 → E0 열 전부."""
    t = pos.copy()
    t["m0"] = t["t0"].dt.floor("min")
    t["p_ref"] = np.nan
    if close is not None:
        x = (t["symbol"] == "XBTUSD").to_numpy()
        t.loc[x, "p_ref"] = price_at(t.loc[x, "m0"], close)
    t = t.join(prev_day_balance(t["t0"], bal))
    day = t["t0"].dt.tz_convert(None).dt.normalize()
    t["e_prev_unreliable"] = day.isin(list(deposits)) | (t["E_prev"] < MIN_E_PREV)
    notional0 = np.where(t["inverse"], t["q0"] / t["p_vwap0"], np.nan)
    t["f0"] = notional0 / t["E_prev"]
    t["f0_raw"] = notional0 / t["E_prev_raw"]
    t["u0"] = t["net_xbt"] / notional0
    return t


def leverage_by_year(lg: pd.DataFrame, bal: pd.DataFrame) -> pd.Series:
    """XBTUSD 일 최대 명목(|체결 후 포지션|/가격, XBT) / 전일 말 보정 잔고 → 연도별 중앙값(T-13 정의)."""
    x = lg[(lg["symbol"] == "XBTUSD") & ~lg["is_settle"]]
    x = x.groupby("fill").last()  # 나뉜 체결은 체결 후 포지션 = 마지막 다리
    notional = x["pos_after"].abs() / x["price"]
    day = x["ts"].dt.tz_convert(None).dt.normalize()
    dmax = notional.groupby(day).max()
    lev = (dmax / bal["bal"].shift(1).reindex(dmax.index)).dropna()
    lev = lev[lev > 0]
    return lev.groupby(lev.index.year).median()


def topk_share(net: pd.Series, k: int) -> tuple[float, float]:
    """손익 상위 k 포지션 비중 — (순손익 분모, |순손익| 분모). 후자가 안정 정의."""
    top = net.sort_values(ascending=False).head(k)
    return float(top.sum() / net.sum()), float(top.abs().sum() / net.abs().sum())


def e0_summary(tab: pd.DataFrame, lg: pd.DataFrame, bal: pd.DataFrame, open_at_end: dict[str, int]) -> dict:
    """설계 문서 E0 완료 기준(재현) + 추가 보고."""
    net = tab["net_xbt"]
    total = float(net.sum())
    inv = tab["inverse"]
    xbt = tab["symbol"] == "XBTUSD"
    r: dict = {
        "positions": len(tab), "positions_xbtusd": int(xbt.sum()), "positions_inverse": int(inv.sum()),
        "win_rate": float((net > 0).mean()), "net_xbt": total,
        "liquidation_fills": int(lg.drop_duplicates("fill")["is_liq"].sum()),
        "lev_med_by_year": {int(y): float(v) for y, v in leverage_by_year(lg, bal).items()},
        "open_at_end": open_at_end,
        "net_share": {"xbtusd": float(net[xbt].sum() / total), "inverse": float(net[inv].sum() / total),
                      "excluded_non_inverse": float(net[~inv].sum() / total)},
        "lt1min_share": float((tab["hold_min"] < 1).mean()),
        "lt1min_share_xbtusd": float((tab.loc[xbt, "hold_min"] < 1).mean()),
        "avgdown": {str(k): {"n": len(g), "win": float((g.net_xbt > 0).mean()), "sum_xbt": float(g.net_xbt.sum())}
                    for k, g in tab.groupby("has_avgdown")},
        "p_ref_missing_xbtusd": int(tab.loc[xbt, "p_ref"].isna().sum()),
        "e_prev_missing_inverse": int(tab.loc[inv, "E_prev"].isna().sum()),
        "e_prev_unreliable_inverse": int(tab.loc[inv, "e_prev_unreliable"].sum()),
        "f0_gt50_inverse": int((tab.loc[inv, "f0"] > 50).sum()),
        "q0_filled_after_t0_inverse": int((tab.loc[inv, "t_q0_end"] > tab.loc[inv, "t0"]).sum()),
        "entry_liq0_inverse": tab.loc[inv, "entry_liq0"].value_counts(dropna=False).to_dict(),
    }
    for k in (10, 100):
        a, b = topk_share(net, k)
        r[f"top{k}_share_net"], r[f"top{k}_share_abs"] = a, b
    q = [0.1, 0.25, 0.5, 0.75, 0.9, 0.99]
    ok = inv & ~tab["e_prev_unreliable"]
    for col, m in (("f0", inv), ("f0_raw", inv), ("f0_reliable", ok)):
        s = tab.loc[m, col.removesuffix("_reliable")].dropna()
        r[f"{col}_quantiles"] = {str(k): float(v) for k, v in s.quantile(q).items()}
        r[f"{col}_n"] = len(s)
    return r


# --- CLI -----------------------------------------------------------------------------------------------------------------

def run_e0(zip_path: Path = DEFAULT_ZIP, out_dir: Path = OUT_DIR) -> dict:
    from src.ingest.store import load_bars

    raw, wallet = read_raw(zip_path)
    ex = prepare_executions(raw)
    lo, hi = pd.Timestamp(SAMPLE_START, tz="UTC"), pd.Timestamp(SAMPLE_END, tz="UTC") + pd.Timedelta(days=1)
    out_of_sample = ~ex["ts"].between(lo, hi, inclusive="left")
    if out_of_sample.any():
        raise ValueError(f"표본 [{lo:%Y-%m-%d}, {hi:%Y-%m-%d}) 밖 체결 {int(out_of_sample.sum())}건 — OOS 미접근 원칙")
    lg, pos, open_at_end = reconstruct(ex)
    bal = wallet_end_balance(wallet)
    close = load_bars(SAMPLE_START, SAMPLE_END).set_index("ts")["close"]
    tab = position_table(pos, bal, close, deposit_days(wallet))
    summary = e0_summary(tab, lg, bal, open_at_end)
    out_dir.mkdir(parents=True, exist_ok=True)
    tab.to_parquet(out_dir / "positions.parquet")
    (out_dir / "e0.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.analysis.aoa_study", description=__doc__.splitlines()[0])
    ap.add_argument("step", choices=["e0"])
    ap.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    if not a.zip.is_file():
        print(f"{a.zip} 없음 — 사람이 받아 둔 aoa 원본 zip 이 필요하다", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(json.dumps(run_e0(a.zip, a.out), indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
