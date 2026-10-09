"""Bybit v5 REST 최소 클라이언트(linear 무기한) — 트레이딩 콘솔용. DEMO·LIVE 는 base URL 만 다르다.

서명: HMAC-SHA256(secret, timestamp + api_key + recv_window + payload), payload = GET 쿼리 문자열 / POST JSON 본문.
응답 `retCode != 0` 이면 `BybitError`. 키·서명은 예외 메시지·로그에 넣지 않는다.
설계: Obsidian `Projects/work/AgentTrading/design/trading-console.md`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal
from collections.abc import Callable, Mapping
from urllib.parse import urlencode

import requests

DEMO_URL = "https://api-demo.bybit.com"
LIVE_URL = "https://api.bybit.com"
BASE_URLS = {"demo": DEMO_URL, "live": LIVE_URL}
CATEGORY = "linear"
RECV_WINDOW = 5000
TIMEOUT = 10
LEVERAGE_NOT_MODIFIED = 110043  # set-leverage: 이미 같은 값
TIMESTAMP_INVALID = 10002      # 요청 시각이 서버 시각과 recv_window 이상 어긋남
MAX_PAGES = 20


class BybitError(RuntimeError):
    def __init__(self, code: int, msg: str, path: str):
        self.code, self.msg, self.path = code, msg, path
        super().__init__(f"Bybit {path} retCode={code}: {msg}")


class Bybit:
    """`session` 은 `requests.Session` 호환(`request(method, url, headers=, data=, timeout=)`) — 테스트에서 주입."""

    def __init__(self, base_url: str, api_key: str | None = None, api_secret: str | None = None, *,
                 session=None, clock: Callable[[], float] = time.time, recv_window: int = RECV_WINDOW):
        self.base_url = base_url.rstrip("/")
        self._key, self._secret = api_key, api_secret
        self._session = session or requests.Session()
        self._clock = clock
        self._recv = str(recv_window)
        self._offset_ms = 0  # 서버 시각 − 로컬 시각(10002 때 보정)

    @property
    def has_keys(self) -> bool:
        return bool(self._key and self._secret)

    def sign(self, timestamp: str, payload: str) -> str:
        msg = f"{timestamp}{self._key}{self._recv}{payload}"
        return hmac.new(self._secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    def _request(self, method: str, path: str, params: Mapping | None = None, auth: bool = False):
        try:
            return self._send(method, path, params, auth)
        except BybitError as e:
            if not (auth and e.code == TIMESTAMP_INVALID):
                raise
            self.sync_time()  # 시각 오차로 거부된 요청은 처리되지 않았다 → 보정 후 1회 재시도
            return self._send(method, path, params, auth)

    def sync_time(self) -> None:
        server_ms = int(self._send("GET", "/v5/market/time", None, False)["timeNano"]) // 1_000_000
        self._offset_ms = server_ms - int(self._clock() * 1000)

    def _send(self, method: str, path: str, params: Mapping | None, auth: bool):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"Content-Type": "application/json"}
        url, body = self.base_url + path, None
        if method == "GET":
            payload = urlencode(params)
            if payload:
                url += "?" + payload
        else:
            payload = body = json.dumps(params, separators=(",", ":"))
        if auth:
            if not self.has_keys:
                raise BybitError(-1, "API 키가 설정되지 않았다", path)
            ts = str(int(self._clock() * 1000) + self._offset_ms)
            headers.update({"X-BAPI-API-KEY": self._key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": self._recv,
                            "X-BAPI-SIGN": self.sign(ts, payload)})
        resp = self._session.request(method, url, headers=headers, data=body, timeout=TIMEOUT)
        try:
            data = resp.json()
        except ValueError:
            raise BybitError(-2, f"HTTP {getattr(resp, 'status_code', '?')} 비JSON 응답", path) from None
        if data.get("retCode") != 0:
            raise BybitError(int(data.get("retCode", -3)), str(data.get("retMsg", "")), path)
        return data.get("result") or {}

    # 공개 시세 ----------------------------------------------------------------------------------------
    def server_time(self) -> dict:
        return self._request("GET", "/v5/market/time")

    def instrument(self, symbol: str) -> dict:
        return self._request("GET", "/v5/market/instruments-info", {"category": CATEGORY, "symbol": symbol})["list"][0]

    def ticker(self, symbol: str) -> dict:
        return self._request("GET", "/v5/market/tickers", {"category": CATEGORY, "symbol": symbol})["list"][0]

    def klines(self, symbol: str, interval: str = "1", limit: int = 200) -> list[list[str]]:
        """[startMs, open, high, low, close, volume, turnover] 최신 → 과거 순."""
        return self._request("GET", "/v5/market/kline",
                             {"category": CATEGORY, "symbol": symbol, "interval": interval, "limit": limit})["list"]

    # 계정·조회 ----------------------------------------------------------------------------------------
    def wallet(self) -> dict:
        return self._request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, auth=True)["list"][0]

    def api_key_info(self) -> dict:
        return self._request("GET", "/v5/user/query-api", auth=True)

    def positions(self, symbol: str) -> list[dict]:
        return self._request("GET", "/v5/position/list", {"category": CATEGORY, "symbol": symbol}, auth=True)["list"]

    def open_orders(self, symbol: str) -> list[dict]:
        return self._request("GET", "/v5/order/realtime", {"category": CATEGORY, "symbol": symbol}, auth=True)["list"]

    def executions(self, symbol: str, limit: int = 50) -> list[dict]:
        return self._request("GET", "/v5/execution/list", {"category": CATEGORY, "symbol": symbol, "limit": limit},
                             auth=True)["list"]

    def closed_pnl(self, symbol: str, start_ms: int | None = None, limit: int = 100) -> list[dict]:
        """실현 손익. `start_ms` 가 있으면 `nextPageCursor` 로 끝까지(최대 `MAX_PAGES` 쪽) 읽는다."""
        out, cursor = [], None
        for _ in range(MAX_PAGES if start_ms is not None else 1):
            res = self._request("GET", "/v5/position/closed-pnl",
                                {"category": CATEGORY, "symbol": symbol, "startTime": start_ms, "limit": limit,
                                 "cursor": cursor}, auth=True)
            out.extend(res.get("list") or [])
            cursor = res.get("nextPageCursor")
            if not cursor:
                break
        else:
            if start_ms is not None and cursor:
                raise BybitError(-4, f"실현 손익이 {MAX_PAGES}쪽을 넘는다 — 일 손실 계산 불가", "/v5/position/closed-pnl")
        return out

    # 주문 ---------------------------------------------------------------------------------------------
    def set_leverage(self, symbol: str, leverage: float) -> None:
        lv = format(Decimal(str(leverage)).normalize(), "f")
        try:
            self._request("POST", "/v5/position/set-leverage",
                          {"category": CATEGORY, "symbol": symbol, "buyLeverage": lv, "sellLeverage": lv}, auth=True)
        except BybitError as e:
            if e.code != LEVERAGE_NOT_MODIFIED:
                raise

    def create_order(self, **params) -> dict:
        return self._request("POST", "/v5/order/create", {"category": CATEGORY, **params}, auth=True)

    def cancel_all(self, symbol: str) -> dict:
        return self._request("POST", "/v5/order/cancel-all", {"category": CATEGORY, "symbol": symbol}, auth=True)

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self._request("POST", "/v5/order/cancel", {"category": CATEGORY, "symbol": symbol, "orderId": order_id},
                             auth=True)
