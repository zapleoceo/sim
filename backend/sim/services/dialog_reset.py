"""Wiping what SIM keeps about a conversation, in one place for every caller.

Three operations, each a part of the next:

- `clear_chat(dialog)`: every message of the dialog, its pending reply / follow-up / passport
  jobs and the dialog counters, so the persona's next reply starts without the past history.
  Used by the owner's «Очистить чат» in the admin (`DELETE /api/dialogs/{id}/messages`).
- `clear_passport(client)`: what was collected about the person: the lead passport, needs,
  notes, next step, and the pending passport updates of all the client's dialogs. Used by the
  owner's «Очистить паспорт» (`DELETE /api/clients/{id}/passport`).
- `reset(dialog, client)`: both, plus the funnel stage and status back to the start. Used by
  `/reset` from a chat (owner's request: testing personas from scratch) and by the game's `НСН`
  (docs/game.md).

Always kept: the client row, its ref code and channel identities (the link to the person stays),
the funnel and partner events (analytics), the request journal, the game state. Nothing is sent
to the chat and nothing is deleted in the messenger itself."""
from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Client, Dialog, Message
from sim.adapters.db.repositories.conversations import DialogRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.domain.enums import ClientStatus, Stage
from sim.services import jobkinds

RESET_COMMAND = "/reset"


def is_reset(text: str | None) -> bool:
    """`/reset` alone (any case, surrounding spaces, a Telegram `@bot` suffix allowed)."""
    word = (text or "").strip().lower().split("@", 1)[0]
    return word == RESET_COMMAND


class DialogReset:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.jobs = JobRepo(session)

    async def clear_chat(self, dialog: Dialog) -> int:
        """Wipe the dialog's history (the caller holds the dialog lock). Returns deleted messages."""
        for kind, key in ((jobkinds.REPLY, jobkinds.reply_key(dialog.id)),
                          (jobkinds.FOLLOWUP, jobkinds.followup_key(dialog.id)),
                          (jobkinds.PASSPORT, jobkinds.passport_key(dialog.id))):
            await self.jobs.cancel_pending(kind, key)
        res = await self.session.execute(delete(Message).where(Message.dialog_id == dialog.id))
        dialog.unread = 0
        dialog.followup_step = 0
        dialog.needs_operator = False
        dialog.last_message_text = ""
        dialog.last_message_direction = None
        dialog.last_message_at = None
        return int(res.rowcount or 0)  # type: ignore[attr-defined]

    async def clear_passport(self, client: Client) -> None:
        """Forget what was collected about the person; the client and its dialogs stay."""
        for dialog in await DialogRepo(self.session).for_client(client.id):
            await self.jobs.cancel_pending(jobkinds.PASSPORT, jobkinds.passport_key(dialog.id))
        client.passport = ""
        client.passport_updated_at = None
        client.needs = []
        client.notes = ""
        client.next_step = ""
        client.next_step_at = None

    async def reset(self, dialog: Dialog, client: Client) -> int:
        """Chat, passport and funnel position (the caller holds the dialog lock).
        Returns deleted messages."""
        wiped = await self.clear_chat(dialog)
        await self.clear_passport(client)
        client.stage = Stage.FIRST_CONTACT
        client.status = ClientStatus.ACTIVE
        client.no_followups = False
        return wiped
