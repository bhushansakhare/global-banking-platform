"""In-process sliding-window rate limiter.

Suitable for the single-node Windows deployment. Replace with a Redis backed
limiter if the API is scaled horizontally (interface kept intentionally small).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


class ProviderThrottle:
    """Minimum interval between calls per provider (per-provider rate limit)."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, provider: str, min_interval: float) -> None:
        if min_interval <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                last = self._last.get(provider, 0.0)
                delay = last + min_interval - now
                if delay <= 0:
                    self._last[provider] = now
                    return
            time.sleep(min(delay, 1.0))


provider_throttle = ProviderThrottle()
