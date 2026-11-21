"""The idempotency scope of the paid AI calls a piece of work makes (docs/broker.md §2b).

A retried task must not pay twice: the Broker adapter sends every queued job with an
`Idempotency-Key`, and the key is derived from the scope the call runs in. The scope lives in
a context variable, so services and ports stay unaware of it:

- each agent task runs in the scope of its Procrastinate job (`job-<id>`): the id survives the
  task's retries and the re-queue of a stalled job, and a deliberate re-run is a new job;
- inside it the key is a hash of the scope and the request: the same request in the same job is
  the same Broker job, a different request (a repair round, another chat) a new one;
- a `sequential` scope (one reply turn) identifies its calls by their order instead of their
  content, because a retried turn rebuilds its prompt with a newer clock: its N-th call is
  still the same logical call and re-attaches to the Broker job of the first attempt.

Outside any scope (the API process) no key is sent."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

SEPARATOR = "/"


@dataclass
class CallScope:
    name: str
    sequential: bool = False
    calls: int = 0

    def next_call(self) -> int:
        """The 1-based position of the next call made in this scope."""
        self.calls += 1
        return self.calls


_current: ContextVar[CallScope | None] = ContextVar("sim_call_scope", default=None)


def current_scope() -> CallScope | None:
    return _current.get()


@contextmanager
def call_scope(name: str, *, sequential: bool = False) -> Iterator[CallScope]:
    """Run the block in a scope nested into the current one (`job-7/reply-3-41`)."""
    parent = _current.get()
    scope = CallScope(f"{parent.name}{SEPARATOR}{name}" if parent is not None else name,
                      sequential=sequential)
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)
