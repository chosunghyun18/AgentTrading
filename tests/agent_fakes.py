"""콘솔 테스트용 모의 거래소 — 실제 Bybit 호출 없음."""

from __future__ import annotations

from src.agent.bybit import BybitError

INSTRUMENT = {"symbol": "BTCUSDT", "status": "Trading",
              "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001", "maxOrderQty": "1500",
                                "maxMktOrderQty": "150", "minNotionalValue": "5"},
              "priceFilter": {"tickSize": "0.10"}, "leverageFilter": {"maxLeverage": "100"}}


class FakeClient:
    """`Bybit` 와 같은 메서드. 주문은 시장가면 즉시 포지션에 반영한다."""

    def __init__(self, price: float = 80_000.0, has_keys: bool = True):
        self.price = price
        self.has_keys = has_keys
        self.base_url = "https://fake"
        self.pos = {"side": "", "size": "0", "avgPrice": "0", "unrealisedPnl": "0", "leverage": "1",
                    "stopLoss": "", "takeProfit": "", "markPrice": str(price), "liqPrice": "", "positionValue": "0"}
        self.orders: list[dict] = []
        self.closed: list[dict] = []
        self.calls: list[tuple] = []
        self.fail: dict[str, BybitError] = {}

    def _call(self, name, **kw):
        self.calls.append((name, kw))
        if name in self.fail:
            raise self.fail[name]

    def server_time(self):
        return {"timeSecond": "1"}

    def instrument(self, symbol):
        return INSTRUMENT

    def ticker(self, symbol):
        return {"lastPrice": str(self.price), "markPrice": str(self.price), "fundingRate": "0.0001",
                "price24hPcnt": "0.01", "volume24h": "1000"}

    def klines(self, symbol, interval="1", limit=200):
        t0 = 1_790_000_000_000
        return [[str(t0 - i * 60_000), str(self.price), str(self.price + 50), str(self.price - 50), str(self.price), "1", "1"]
                for i in range(limit)]

    def wallet(self):
        self._call("wallet")
        return {"totalEquity": "1000", "totalAvailableBalance": "900", "totalPerpUPL": "0",
                "coin": [{"coin": "USDT", "walletBalance": "1000", "equity": "1000"}]}

    def api_key_info(self):
        self._call("api_key_info")
        return {"readOnly": 0, "permissions": {"ContractTrade": ["Order", "Position"], "Wallet": []}, "ips": ["*"]}

    def positions(self, symbol):
        return [dict(self.pos)]

    def open_orders(self, symbol):
        return list(self.orders)

    def executions(self, symbol, limit=50):
        return [{"execTime": "1790000000000", "side": "Buy", "execPrice": str(self.price), "execQty": "0.01",
                 "execFee": "0.4", "orderType": "Market", "orderLinkId": "atc-x"}]

    def closed_pnl(self, symbol, start_ms=None, limit=100):
        return list(self.closed)

    def set_leverage(self, symbol, leverage):
        self._call("set_leverage", leverage=leverage)

    def create_order(self, **params):
        self._call("create_order", **params)
        if params["orderType"] == "Limit":
            self.orders.append({"orderId": f"o{len(self.orders)}", **params, "orderStatus": "New"})
            return {"orderId": f"o{len(self.orders) - 1}", "orderLinkId": params["orderLinkId"]}
        qty = float(params["qty"]) * (1 if params["side"] == "Buy" else -1)
        cur = float(self.pos["size"]) * (1 if self.pos["side"] == "Buy" else -1)
        new = cur + qty
        self.pos.update(side="Buy" if new > 0 else ("Sell" if new < 0 else ""), size=f"{abs(new):g}",
                        avgPrice=str(self.price), stopLoss=params.get("stopLoss", ""))
        return {"orderId": "m1", "orderLinkId": params["orderLinkId"]}

    def cancel_all(self, symbol):
        self._call("cancel_all")
        n, self.orders = len(self.orders), []
        return {"list": [{}] * n}

    def cancel_order(self, symbol, order_id):
        self._call("cancel_order", order_id=order_id)
        self.orders = [o for o in self.orders if o["orderId"] != order_id]
        return {"orderId": order_id}
