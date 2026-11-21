"""Plan executor: one handler per action type (docs/reply.md, «Выполнение плана»)."""
from __future__ import annotations

from sim.services.actions.base import ActionContext, ActionHandler, ApplyResult
from sim.services.actions.registry import HANDLERS, handler_for

__all__ = ["HANDLERS", "ActionContext", "ActionHandler", "ApplyResult", "handler_for"]
