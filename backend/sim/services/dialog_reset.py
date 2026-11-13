"""`/reset` from a client: wipe the dialog's history and everything the persona learned, so the
next message starts a brand-new conversation (owner's request: testing personas from scratch).

What is wiped: every message of the dialog, the pending reply / follow-up jobs, the dialog
counters, and the client's conversation context (stage, status, needs, notes, next step).
Kept: the client row, its ref code and channel identities (the link to the person stays), the
funnel and partner events (analytics), the request journal. Nothing is sent to the chat and
nothing is deleted in the messenger itself."""
from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.db.models import Client, Dialog, Message
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

    async def reset(self, dialog: Dialog, client: Client) -> int:
        """Wipe the dialog (the caller holds the dialog lock). Returns deleted messages."""
        for kind, key in ((jobkinds.REPLY, jobkinds.reply_key(dialog.id)),
                          (jobkinds.FOLLOWUP, jobkinds.followup_key(dialog.id))):
            await self.jobs.cancel_pending(kind, key)
        res = await self.session.execute(delete(Message).where(Message.dialog_id == dialog.id))
        dialog.unread = 0
        dialog.followup_step = 0
        dialog.needs_operator = False
        dialog.last_message_text = ""
        dialog.last_message_direction = None
        dialog.last_message_at = None
        client.stage = Stage.FIRST_CONTACT
        client.status = ClientStatus.ACTIVE
        client.needs = []
        client.notes = ""
        client.next_step = ""
        client.next_step_at = None
        client.no_followups = False
        return int(res.rowcount or 0)  # type: ignore[attr-defined]
