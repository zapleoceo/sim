"""The executor's contract: one handler per plan action (spec §7). A new action type is a new
handler registered in sim/services/actions/registry.py; the others do not change.

Three steps per action:
- `busy` names the Telegram status for the action's slow work (TTS, a generation), shown before
  that work starts (sim/domain/presence.py); None = the send task's short action is enough;
- `prepare` runs outside any database transaction and does the slow work that must finish
  before the turn is committed (e.g. TTS for a voice note);
- `apply` runs inside the turn's transaction (chat row locked) and only writes rows: pending
  outgoing messages and generation jobs, in plan order."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Persona
from sim.domain.presence import Busy
from sim.domain.writing import DEFAULT_MODE, WritingMode
from sim.services.deps import Deps


@dataclass
class ActionContext:
    deps: Deps
    persona: Persona
    chat_id: int
    reply_run_id: int
    writing: WritingMode = DEFAULT_MODE     # how she types now: the «phone» of text messages


@dataclass
class ApplyResult:
    """What the turn must hand to the queue after commit, in order."""

    message_ids: list[int] = field(default_factory=list)
    generation_ids: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # degraded steps, stored in run.error


class ActionHandler(ABC):
    action_type: ClassVar[type[BaseModel]]

    def busy(self, action: Any) -> Busy | None:
        """The status shown while this action's slow work runs; None = nothing slow."""
        return None

    async def prepare(self, action: Any, ctx: ActionContext) -> Any:
        """Slow work before the transaction; the result is passed to `apply`."""
        return None

    @abstractmethod
    async def apply(self, session: AsyncSession, action: Any, prepared: Any,
                    ctx: ActionContext, result: ApplyResult) -> None:
        """Write the rows for this action (inside the turn's transaction)."""
