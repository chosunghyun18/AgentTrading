"""Bybit v5 WebSocket 스트림(`websockets` asyncio) — 구독·20 s 앱 ping·재연결·개인 인증.

엔드포인트(pybit 소스 확인, 설계 11절): 공개는 메인넷 시세(demo 도 같음), 개인은 demo/live 별.
개인 인증: `{"op":"auth","args":[key, expires_ms, HMAC_SHA256(secret, "GET/realtime" + expires_ms)]}` —
`success` 응답을 받은 뒤 구독한다. expires 는 거래소 시각 추정값으로 만든다(로컬 시계 오차 대비).
연결이 죽으면 프로토콜 ping(`websockets` keepalive) 시간 초과로 끊기고, 지수 백오프(최대 30 s)로 다시 붙는다.
`on_state(True/False)` 로 연결 상태를 알린다 — 끊기면 상태 캐시·봉 생성기가 의심 구간을 표시한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Callable

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

PUBLIC_URL = "wss://stream.bybit.com/v5/public/linear"
PRIVATE_URLS = {"demo": "wss://stream-demo.bybit.com/v5/private", "live": "wss://stream.bybit.com/v5/private"}
PRIVATE_TOPICS = ["position", "order", "execution", "wallet"]
PING_S = 20
AUTH_TIMEOUT_S = 10
AUTH_TTL_MS = 10_000
BACKOFF_MAX_S = 30


class AuthError(RuntimeError):
    """개인 WS 인증 거부 — 키 문제라 재시도하지 않는다."""


class StreamError(RuntimeError):
    pass


def auth_message(api_key: str, api_secret: str, expires_ms: int) -> dict:
    sig = hmac.new(api_secret.encode(), f"GET/realtime{expires_ms}".encode(), hashlib.sha256).hexdigest()
    return {"op": "auth", "args": [api_key, expires_ms, sig]}


async def _pinger(ws, every_s: float) -> None:
    n = 0
    while True:
        await asyncio.sleep(every_s)
        n += 1
        await ws.send(json.dumps({"op": "ping", "req_id": f"p{n}"}))


async def run_stream(url: str, topics: list[str], on_message: Callable[[dict], None], *,
                     auth: tuple[str, str] | None = None, clock_ms: Callable[[], float] | None = None,
                     on_state: Callable[[bool], None] = lambda ok: None, connect=ws_connect,
                     ping_s: float = PING_S, backoff_max_s: float = BACKOFF_MAX_S,
                     max_connects: int | None = None, sleep=asyncio.sleep) -> None:
    """취소(또는 `max_connects` 소진)될 때까지 메시지를 `on_message` 로 넘긴다."""
    attempt = connects = 0
    while max_connects is None or connects < max_connects:
        connects += 1
        try:
            async with connect(url) as ws:
                if auth:
                    expires = int((clock_ms() if clock_ms else 0) + AUTH_TTL_MS)
                    await ws.send(json.dumps(auth_message(*auth, expires)))
                    resp = json.loads(await asyncio.wait_for(ws.recv(), AUTH_TIMEOUT_S))
                    if not resp.get("success"):
                        raise AuthError(f"개인 WS 인증 실패: {resp.get('ret_msg', '')}")
                await ws.send(json.dumps({"op": "subscribe", "req_id": "sub", "args": topics}))
                on_state(True)
                attempt = 0
                pinger = asyncio.create_task(_pinger(ws, ping_s))
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if "topic" in msg:
                            on_message(msg)
                        elif msg.get("op") == "subscribe" and msg.get("success") is False:
                            raise StreamError(f"구독 실패: {msg.get('ret_msg', '')}")
                finally:
                    pinger.cancel()
        except AuthError:
            on_state(False)
            raise
        except (OSError, WebSocketException, StreamError, asyncio.TimeoutError, ValueError):
            pass
        on_state(False)
        if max_connects is not None and connects >= max_connects:
            break
        await sleep(min(backoff_max_s, 2 ** attempt))
        attempt += 1
