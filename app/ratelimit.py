"""Tiny async limiter so background stages stay inside free-tier RPM."""

import asyncio
import threading
import time


class SyncRateLimiter:
    """Same idea for code running in worker threads."""

    def __init__(self, per_minute: int):
        self.interval = 60.0 / max(per_minute, 1)
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = self.next_at - now
            self.next_at = max(now, self.next_at) + self.interval
        if delay > 0:
            time.sleep(delay)


class RateLimiter:
    def __init__(self, per_minute: int):
        self.interval = 60.0 / max(per_minute, 1)
        self.next_at = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            now = time.monotonic()
            delay = self.next_at - now
            self.next_at = max(now, self.next_at) + self.interval
        if delay > 0:
            await asyncio.sleep(delay)
