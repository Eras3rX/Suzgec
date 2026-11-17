"""Ограничение частоты запросов: скользящее окно по ключу (IP клиента), только в памяти."""
from __future__ import annotations

import math
import time
from collections import deque


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> tuple[bool, int]:
        """Вернуть (разрешено, через сколько секунд повторить)."""
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False, max(1, math.ceil(self.window - (now - hits[0])))
        hits.append(now)
        if len(self._hits) > 10_000:  # не даём словарю расти бесконечно
            self._sweep(now)
        return True, 0

    def _sweep(self, now: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] >= self.window]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()
