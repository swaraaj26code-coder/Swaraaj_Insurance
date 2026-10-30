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


async def map_threads(fn, args: list[tuple], limit: int) -> list:
    """fn(*a) for each a in worker threads, at most `limit` at a time; results in input order.
    For blocking HTTP lookups that used to run one after another (FAILURES #16)."""
    sem = asyncio.Semaphore(max(limit, 1))

    async def one(a):
        async with sem:
            return await asyncio.to_thread(fn, *a)
    return await asyncio.gather(*(one(a) for a in args))


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
