"""In-memory limit of FAILED dashboard logins (one API process, so no shared store is needed)."""
from __future__ import annotations

import time
from collections import OrderedDict, deque


class TooManyAttempts(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__("Слишком много попыток входа, попробуйте позже")
        self.retry_after_s = retry_after_s


class LoginRateLimiter:
    """Sliding window of failed attempts, keyed by (telegram id, ip) and by ip alone.

    There is deliberately no key by telegram id alone: it would let anyone lock the owner out
    from anywhere. The pair key stops hammering one identity from one address; the looser ip
    key stops one address from spraying many identities. The key table is bounded."""

    def __init__(self, max_failures: int = 5, window_s: int = 900, *,
                 ip_max_failures: int = 20, max_keys: int = 10_000) -> None:
        self.max_failures = max_failures
        self.ip_max_failures = ip_max_failures
        self.window_s = window_s
        self.max_keys = max_keys
        self.failures: OrderedDict[str, deque[float]] = OrderedDict()

    @staticmethod
    def keys_for(subject: str, ip: str) -> tuple[str, str]:
        return f"pair:{subject.lower()}|{ip}", f"ip:{ip}"

    def limit_for(self, key: str) -> int:
        return self.ip_max_failures if key.startswith("ip:") else self.max_failures

    def check(self, *keys: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for key in keys:
            bucket = self.failures.get(key)
            if bucket is None:
                continue
            while bucket and now - bucket[0] > self.window_s:
                bucket.popleft()
            if len(bucket) >= self.limit_for(key):
                raise TooManyAttempts(int(self.window_s - (now - bucket[0])) + 1)

    def record_failure(self, *keys: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for key in keys:
            self.failures.setdefault(key, deque()).append(now)
            self.failures.move_to_end(key)
        if len(self.failures) > self.max_keys:
            for key in [k for k, b in self.failures.items()
                        if not b or now - b[-1] > self.window_s]:
                del self.failures[key]
        while len(self.failures) > self.max_keys:
            self.failures.popitem(last=False)

    def reset(self, *keys: str) -> None:
        for key in keys:
            self.failures.pop(key, None)
