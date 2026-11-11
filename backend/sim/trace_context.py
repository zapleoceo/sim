"""The current trace (system log correlation id) for this task, in a ContextVar.

Dependency-free so any layer can read it: the job queue stores it on enqueue, the LLM audit
stamps it on llm_call, the trace log writes it on every step (sim.services.tracing)."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


def new_trace_id() -> str:
    return uuid.uuid4().hex


@dataclass
class TraceContext:
    """The current trace and the links every step inherits (mutable: the auth dependency sets
    the admin, the worker sets the dialog/job). Steps recorded inside a scope wait in `pending`
    and are written after the unit of work ends (sim.services.tracing.Tracer.flush): the log
    then never competes with the caller's open write transaction."""

    trace_id: str
    actor: str = ""
    links: dict[str, int] = field(default_factory=dict)
    pending: list[Any] = field(default_factory=list)

    def link(self, **links: int | None) -> None:
        self.links.update({k: v for k, v in links.items() if v is not None})


_current: ContextVar[TraceContext | None] = ContextVar("sim_trace", default=None)


def current() -> TraceContext | None:
    return _current.get()


def current_trace_id() -> str | None:
    ctx = _current.get()
    return ctx.trace_id if ctx else None


@contextmanager
def trace_scope(trace_id: str | None = None, *, actor: str = "", **links: int | None) -> Iterator[TraceContext]:
    """Make a trace current for this block (a new id when none is given)."""
    ctx = TraceContext(trace_id or new_trace_id(), actor)
    ctx.link(**links)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)

