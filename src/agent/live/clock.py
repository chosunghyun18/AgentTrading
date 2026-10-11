"""거래소 시각 추정 — 로컬 단조 시계 + 서버 시각 오프셋.

벽시계(`time.time`)는 NTP 보정으로 뛸 수 있어 기준 시점 이후 경과는 단조 시계로 잰다.
오프셋은 `/v5/market/time` 왕복의 중간 시점 기준이라 왕복 지연 절반만큼 오차가 있다(설계 5절 "시각 기준").
"""

from __future__ import annotations

import time
from collections.abc import Callable


class ExchangeClock:
    def __init__(self, wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic):
        self._mono = mono
        self._base_mono = mono()
        self._base_ms = wall() * 1000
        self.offset_ms = 0.0     # 서버 − 로컬
        self.rtt_ms: float | None = None

    def local_ms(self) -> float:
        return self._base_ms + (self._mono() - self._base_mono) * 1000

    def now_ms(self) -> float:
        """추정 거래소 시각(ms)."""
        return self.local_ms() + self.offset_ms

    def sync(self, fetch_server_ms: Callable[[], int]) -> float:
        """서버 시각 1회 조회로 오프셋을 맞춘다. 반환: 왕복 지연(ms)."""
        before = self.local_ms()
        server = fetch_server_ms()
        after = self.local_ms()
        self.rtt_ms = after - before
        self.offset_ms = server - (before + after) / 2
        return self.rtt_ms

    def sync_best(self, fetch_server_ms: Callable[[], int], samples: int = 5) -> float:
        """여러 번 조회해 왕복 지연이 가장 짧은 표본의 오프셋을 쓴다(NTP 방식). 첫 조회는 DNS·TLS 연결 수립이
        섞여 왕복이 길다. 반환: 채택한 왕복 지연(ms)."""
        best: tuple[float, float] | None = None
        for _ in range(max(1, samples)):
            rtt = self.sync(fetch_server_ms)
            if best is None or rtt < best[0]:
                best = (rtt, self.offset_ms)
        self.rtt_ms, self.offset_ms = best
        return self.rtt_ms
