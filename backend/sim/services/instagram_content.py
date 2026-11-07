"""Opt-in single-photo publisher. Never downloads user-supplied URLs."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.channels.capabilities import Capability
from sim.adapters.crypto import SecretBox
from sim.adapters.db.models import ContentPost
from sim.adapters.db.repositories.catalog import ChannelRepo, PersonaRepo
from sim.adapters.media import SAFE_NAME, MediaStore
from sim.connectors.spec import Connector
from sim.domain.enums import ChannelKind, ContentKind, SessionStatus
from sim.services.content import ContentError

UNCERTAIN = "Instagram publish outcome uncertain; reconcile the account before retrying"


class InstagramContentPublisher:
    def __init__(
        self, session: AsyncSession, registry: Mapping[ChannelKind, Connector],
        box: SecretBox, store: MediaStore, allowed_post_ids: tuple[int, ...],
    ) -> None:
        self.channels = ChannelRepo(session)
        self.personas = PersonaRepo(session)
        self.registry, self.box, self.store = registry, box, store
        self.allowed_post_ids = allowed_post_ids

    def photo_path(self, post: ContentPost) -> Path:
        if post.kind != ContentKind.PHOTO or len(post.media_urls) != 1:
            raise ContentError("Instagram publishing requires exactly one photo")
        ref = post.media_urls[0]
        prefix = "/api/media/"
        if not isinstance(ref, str) or not ref.startswith(prefix):
            raise ContentError("Use a local /api/media/ JPEG reference")
        name = ref[len(prefix):]
        if not SAFE_NAME.fullmatch(name) or Path(name).suffix.lower() not in (".jpg", ".jpeg"):
            raise ContentError("Use a local /api/media/ JPEG reference")
        root = self.store.root.resolve()
        path = root / name
        if path.is_symlink() or path.resolve().parent != root or not path.is_file():
            raise ContentError("Local photo is missing or unsafe")
        # Fail before building the connector, including for renamed non-JPEG files.
        with path.open("rb") as stream:
            if stream.read(3) != b"\xff\xd8\xff":
                raise ContentError("Local photo must contain JPEG data")
        return path

    async def publish(self, post: ContentPost) -> None:
        if post.id not in self.allowed_post_ids:
            raise ContentError("Post is not explicitly enabled for Instagram publication")
        persona = await self.personas.get(post.persona_id)
        if persona is None or not persona.is_active:
            raise ContentError("Persona is unavailable for publication")
        path = self.photo_path(post)
        if post.channel_id is None:
            raise ContentError("Select an explicit Instagram channel")
        channel = await self.channels.get(post.channel_id)
        if (channel is None or channel.kind != ChannelKind.INSTAGRAM
                or channel.persona_id != post.persona_id or channel.is_archived
                or channel.status != SessionStatus.ACTIVE or not channel.session_enc):
            raise ContentError("Selected Instagram channel is unavailable for this persona")
        connector = self.registry.get(ChannelKind.INSTAGRAM)
        if connector is None:
            raise ContentError("Instagram connector is unavailable")
        try:
            session = self.box.decrypt_json(channel.session_enc)
            if not session:
                raise ValueError("empty session")
            port = connector.build_port(channel.id, session)
        except Exception:
            raise ContentError("Instagram session is unavailable") from None
        if Capability.PUBLISH_PHOTO not in getattr(port, "capabilities", frozenset()):
            raise ContentError("Instagram channel does not support photo publication")
        try:
            external_id = await port.publish_photo(path, post.caption)  # type: ignore[attr-defined]
            if not isinstance(external_id, str) or not external_id.strip():
                raise ValueError("empty media id")
        except Exception:
            # Never expose adapter exceptions, tokens, proxy credentials, or URLs.
            raise ContentError(UNCERTAIN) from None
