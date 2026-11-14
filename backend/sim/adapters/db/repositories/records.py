"""Partner events and content posts."""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select

from sim.adapters.db.models import ContentPost, PartnerEvent
from sim.adapters.db.repositories.base import Repository
from sim.domain.enums import PartnerEventKind


class PartnerEventRepo(Repository[PartnerEvent]):
    model = PartnerEvent

    async def find(self, kind: PartnerEventKind, external_id: str) -> PartnerEvent | None:
        return await self.session.scalar(
            select(PartnerEvent).where(
                PartnerEvent.kind == kind, PartnerEvent.external_id == external_id
            )
        )

    async def for_client(self, client_id: int) -> Sequence[PartnerEvent]:
        return (
            await self.session.scalars(
                select(PartnerEvent)
                .where(PartnerEvent.client_id == client_id)
                .order_by(PartnerEvent.occurred_at)
            )
        ).all()


class ContentRepo(Repository[ContentPost]):
    model = ContentPost

    async def for_publish(self, post_id: int) -> ContentPost | None:
        """Serialize publishers on PostgreSQL and refresh a previously loaded ORM row."""
        return await self.session.scalar(
            select(ContentPost).where(ContentPost.id == post_id).with_for_update()
            .execution_options(populate_existing=True)
        )

    async def listing(self) -> Sequence[ContentPost]:
        return (
            await self.session.scalars(select(ContentPost).order_by(ContentPost.id.desc()))
        ).all()
