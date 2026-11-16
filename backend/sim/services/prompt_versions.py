"""Versions of a persona's prompt (`persona.agent_prompt`).

Every saved text that differs from the current one becomes a version: who saved it and when.
The newest version is the prompt in use. A version is never edited; «Восстановить» saves the old
text again as a new version, so the history only grows and shows every switch. Writers: the
persona form (`POST`/`PUT /api/personas`), the restore and `apply-persona-prompts`."""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Persona, PersonaPromptVersion
from sim.clock import Clock, utcnow

AUTHOR_MAX = 120
NOTE_MAX = 200
BEFORE_HISTORY = "версия до включения истории"


class VersionNotFound(LookupError):
    """No such version of this persona's prompt."""


class PromptVersions:
    def __init__(self, session: AsyncSession, *, clock: Clock = utcnow) -> None:
        self.session = session
        self.clock = clock

    async def history(self, persona_id: int) -> Sequence[PersonaPromptVersion]:
        """Newest first."""
        return (await self.session.scalars(
            select(PersonaPromptVersion)
            .where(PersonaPromptVersion.persona_id == persona_id)
            .order_by(PersonaPromptVersion.id.desc())
        )).all()

    async def latest(self, persona_id: int) -> PersonaPromptVersion | None:
        return await self.session.scalar(
            select(PersonaPromptVersion)
            .where(PersonaPromptVersion.persona_id == persona_id)
            .order_by(PersonaPromptVersion.id.desc())
            .limit(1)
        )

    async def get(self, persona_id: int, version_id: int) -> PersonaPromptVersion:
        row = await self.session.get(PersonaPromptVersion, version_id)
        if row is None or row.persona_id != persona_id:
            raise VersionNotFound(version_id)
        return row

    async def save(self, persona: Persona, text: str, *, author: str = "", note: str = "") -> bool:
        """Set the persona's prompt and add a version when the text changed. A persona without
        any version yet first gets its previous text as one, so the history does not start
        mid-way. False: the text is the current one, nothing was added."""
        latest = await self.latest(persona.id)
        previous = persona.agent_prompt or ""
        persona.agent_prompt = text
        if latest is not None and latest.text == text:
            return False
        if latest is None and previous and previous != text:
            self.add(persona.id, previous, "", BEFORE_HISTORY)
        self.add(persona.id, text, author, note)
        await self.session.flush()
        return True

    def add(self, persona_id: int, text: str, author: str, note: str) -> None:
        self.session.add(PersonaPromptVersion(
            persona_id=persona_id, text=text, author=author[:AUTHOR_MAX], note=note[:NOTE_MAX],
            created_at=self.clock()))



def restored_note(old: PersonaPromptVersion) -> str:
    """The note of a version that puts an older text back in use."""
    return f"восстановлена версия от {old.created_at:%d.%m.%Y %H:%M} UTC"
