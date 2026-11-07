"""Clients, identities, dialogs, messages, funnel events."""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, or_, select

from sim.adapters.db.models import (
    Channel,
    Client,
    ClientIdentity,
    Dialog,
    FunnelEvent,
    Message,
)
from sim.adapters.db.repositories.base import Repository
from sim.domain.enums import ChannelKind, ClientStatus, Delivery, Direction, Stage


def like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class ClientRepo(Repository[Client]):
    model = Client

    async def by_ref(self, ref_code: str) -> Client | None:
        return await self.session.scalar(select(Client).where(Client.ref_code == ref_code))

    async def by_identity(self, channel_id: int, external_id: str) -> Client | None:
        return await self.session.scalar(
            select(Client)
            .join(ClientIdentity, ClientIdentity.client_id == Client.id)
            .where(ClientIdentity.channel_id == channel_id, ClientIdentity.external_id == external_id)
        )

    async def by_phone(self, persona_id: int, phone: str) -> Client | None:
        return await self.session.scalar(
            select(Client)
            .where(Client.persona_id == persona_id, Client.phone == phone)
            .order_by(Client.id)
            .limit(1)
        )

    async def identities_of(self, client_id: int) -> Sequence[ClientIdentity]:
        return (
            await self.session.scalars(
                select(ClientIdentity).where(ClientIdentity.client_id == client_id)
            )
        ).all()

    async def add_identity(
        self, client: Client, channel_id: int, kind: ChannelKind, external_id: str
    ) -> ClientIdentity:
        identity = ClientIdentity(
            client_id=client.id, channel_id=channel_id, channel_kind=kind, external_id=external_id
        )
        self.session.add(identity)
        await self.session.flush()
        return identity

    def filtered(
        self,
        *,
        stage: Stage | None = None,
        status: ClientStatus | None = None,
        persona_id: int | None = None,
        q: str | None = None,
    ):  # noqa: ANN201 - sqlalchemy Select
        stmt = select(Client)
        if stage:
            stmt = stmt.where(Client.stage == stage)
        if status:
            stmt = stmt.where(Client.status == status)
        if persona_id:
            stmt = stmt.where(Client.persona_id == persona_id)
        if q:
            pattern = like(q)
            stmt = stmt.where(
                or_(
                    Client.display_name.ilike(pattern, escape="\\"),
                    Client.handle.ilike(pattern, escape="\\"),
                    Client.phone.ilike(pattern, escape="\\"),
                    Client.notes.ilike(pattern, escape="\\"),
                )
            )
        return stmt.order_by(Client.last_contact_at.desc(), Client.id.desc())

    async def search(
        self, limit: int, offset: int, **filters: object
    ) -> tuple[Sequence[Client], int]:
        return await self.page(self.filtered(**filters), limit, offset)  # type: ignore[arg-type]

    async def for_funnel(self, persona_id: int | None) -> Sequence[Client]:
        stmt = select(Client).order_by(Client.last_contact_at.desc(), Client.id.desc())
        if persona_id:
            stmt = stmt.where(Client.persona_id == persona_id)
        return (await self.session.scalars(stmt)).all()


class DialogRepo(Repository[Dialog]):
    model = Dialog

    async def by_thread(self, channel_id: int, thread_id: str) -> Dialog | None:
        return await self.session.scalar(
            select(Dialog).where(
                Dialog.channel_id == channel_id, Dialog.external_thread_id == thread_id
            )
        )

    async def for_client(self, client_id: int) -> Sequence[Dialog]:
        return (
            (await self.session.scalars(select(Dialog).where(Dialog.client_id == client_id)))
            .unique()
            .all()
        )

    async def search(  # noqa: PLR0913
        self,
        limit: int,
        offset: int,
        *,
        channel_kind: ChannelKind | None = None,
        persona_id: int | None = None,
        stage: Stage | None = None,
        status: ClientStatus | None = None,
        q: str | None = None,
        unread_only: bool = False,
        needs_operator: bool = False,
    ) -> tuple[Sequence[Dialog], int]:
        stmt = (
            select(Dialog)
            .join(Client, Client.id == Dialog.client_id)
            .join(Channel, Channel.id == Dialog.channel_id)
        )
        if channel_kind:
            stmt = stmt.where(Channel.kind == channel_kind)
        if persona_id:
            stmt = stmt.where(Client.persona_id == persona_id)
        if stage:
            stmt = stmt.where(Client.stage == stage)
        if status:
            stmt = stmt.where(Client.status == status)
        if unread_only:
            stmt = stmt.where(Dialog.unread > 0)
        if needs_operator:
            stmt = stmt.where(Dialog.needs_operator.is_(True))
        if q:
            pattern = like(q)
            stmt = stmt.where(
                or_(
                    Client.display_name.ilike(pattern, escape="\\"),
                    Client.handle.ilike(pattern, escape="\\"),
                    Dialog.last_message_text.ilike(pattern, escape="\\"),
                )
            )
        stmt = stmt.order_by(Dialog.last_message_at.desc(), Dialog.id.desc())
        return await self.page(stmt, limit, offset)


class MessageRepo(Repository[Message]):
    model = Message

    async def by_external(self, dialog_id: int, external_id: str) -> Message | None:
        return await self.session.scalar(
            select(Message).where(Message.dialog_id == dialog_id, Message.external_id == external_id)
        )

    async def recent(self, dialog_id: int, limit: int) -> list[Message]:
        """Last `limit` messages, oldest first."""
        rows = (
            await self.session.scalars(
                select(Message)
                .where(Message.dialog_id == dialog_id)
                .order_by(Message.id.desc())
                .limit(limit)
            )
        ).all()
        return list(reversed(rows))

    async def recent_visible(self, dialog_id: int, limit: int) -> list[Message]:
        """Client-visible history; filter before limiting so drafts do not evict real turns."""
        rows = (
            await self.session.scalars(
                select(Message)
                .where(
                    Message.dialog_id == dialog_id,
                    or_(Message.direction == Direction.IN, Message.delivery == Delivery.SENT),
                    or_(Message.guard_verdict.is_(None), Message.guard_verdict != "blocked"),
                )
                .order_by(Message.id.desc())
                .limit(limit)
            )
        ).all()
        return list(reversed(rows))

    async def last(self, dialog_id: int, direction: Direction | None = None) -> Message | None:
        stmt = select(Message).where(Message.dialog_id == dialog_id)
        if direction:
            stmt = stmt.where(Message.direction == direction)
        return await self.session.scalar(stmt.order_by(Message.id.desc()).limit(1))

    async def count(self, dialog_id: int) -> int:
        return int(
            await self.session.scalar(
                select(func.count(Message.id)).where(Message.dialog_id == dialog_id)
            )
            or 0
        )


class FunnelEventRepo(Repository[FunnelEvent]):
    model = FunnelEvent

    async def for_client(self, client_id: int) -> Sequence[FunnelEvent]:
        return (
            await self.session.scalars(
                select(FunnelEvent)
                .where(FunnelEvent.client_id == client_id)
                .order_by(FunnelEvent.id.desc())
            )
        ).all()
