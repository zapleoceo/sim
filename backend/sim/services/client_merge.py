"""The operator merges two clients into one (docs/leads.md, «Один человек в разных
мессенджерах»). One transaction: the other client's identities and chats move to the kept one —
with them everything that lives on a chat (messages, journal, day and week diary, notes of the
day, calls, planned contacts); the profile and the passport are combined (sim.domain.client_merge,
sim.domain.passport.merge_passports); every chat gets a `client_merged` journal line; the other
client row is deleted. Nothing is lost, and nothing is merged by itself."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sim.db.models import Chat, Client, ClientIdentity
from sim.domain.client_merge import Profile, merge_profiles, merge_refusal
from sim.domain.enums import EventKind
from sim.domain.passport import merge_passports, passport_of, stored
from sim.events import append_event


class MergeRefused(ValueError):
    """The merge is not possible (the message is Russian, for the operator)."""


class ClientMissing(LookupError):
    pass


@dataclass(frozen=True)
class MergeResult:
    client: Client
    moved_chat_ids: list[int]


def _profile(client: Client) -> Profile:
    return Profile(name=client.name, username=client.username, phone=client.phone,
                   timezone=client.timezone, notes=client.notes,
                   last_seen_at=client.last_seen_at)


async def merge_clients(session: AsyncSession, keep_id: int, other_id: int, *, actor: str,
                        now: datetime) -> MergeResult:
    """Merge `other_id` into `keep_id` (inside the caller's transaction). The kept client keeps
    its persona and its AI switch. Raises ClientMissing / MergeRefused."""
    rows = list(await session.scalars(
        select(Client).where(Client.id.in_({keep_id, other_id})).order_by(Client.id)
        .with_for_update()))
    by_id = {c.id: c for c in rows}
    keep, other = by_id.get(keep_id), by_id.get(other_id)
    if keep is None or other is None:
        raise ClientMissing(f"client {keep_id if keep is None else other_id} not found")
    refusal = merge_refusal(keep.id, other.id, [i.channel_kind for i in keep.identities],
                            [i.channel_kind for i in other.identities])
    if refusal is not None:
        raise MergeRefused(refusal)

    for field, value in asdict(merge_profiles(_profile(keep), _profile(other))).items():
        setattr(keep, field, value)
    keep.passport = stored(merge_passports(passport_of(keep.passport),
                                           passport_of(other.passport), now))
    other_name = other.name or (f"@{other.username}" if other.username else f"#{other.id}")
    moved = list(await session.scalars(select(Chat.id).where(Chat.client_id == other.id)))
    # Bulk updates, then the row is deleted by id: the ORM's delete-orphan cascade of
    # `identities` must not see the moved identities as orphans.
    await session.execute(update(ClientIdentity).where(ClientIdentity.client_id == other.id)
                          .values(client_id=keep.id))
    await session.execute(update(Chat).where(Chat.client_id == other.id)
                          .values(client_id=keep.id))
    session.expunge(other)
    await session.execute(delete(Client).where(Client.id == other_id))
    await session.flush()
    await session.refresh(keep, ["identities"])
    chat_ids = list(await session.scalars(
        select(Chat.id).where(Chat.client_id == keep.id).order_by(Chat.id)))
    for chat_id in chat_ids:
        await append_event(session, chat_id=chat_id, kind=EventKind.CLIENT_MERGED, actor=actor,
                           data={"from_client_id": other_id, "from_name": other_name,
                                 "into_client_id": keep.id, "moved": chat_id in moved})
    return MergeResult(client=keep, moved_chat_ids=moved)
