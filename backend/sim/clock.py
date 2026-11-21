"""Time source. Services never call datetime.now() directly: tests pass a FixedClock."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class FixedClock:
    at: datetime

    def now(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> None:
        self.at = self.at + timedelta(**delta)
