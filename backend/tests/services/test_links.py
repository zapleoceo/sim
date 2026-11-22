"""A TikTok / Reels / Shorts link she watches (docs/reply.md, «Ролик по ссылке»): the inbox
gives the text message a pending clip, understanding downloads and describes it, the timeline
shows what she saw — or that it did not open."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sim.adapters.link_video import ydl_options
from sim.clock import FixedClock
from sim.db.models import Channel, Chat, MediaAsset, Message
from sim.domain.enums import (
    Author,
    Direction,
    MediaKind,
    MediaSource,
    MessageKind,
    MessageStatus,
    UnderstandingStatus,
)
from sim.domain.links import LINK_KEY_PREFIX, is_link_key, video_link
from sim.ports import FetchedVideo, InboundMessage, Peer, PortError
from sim.services.context.render import LINK_LABEL, incoming_media, media_label
from sim.services.deps import Deps
from sim.services.inbox import Inbox
from sim.services.understanding import Understanding
from tests.services.conftest import Fakes
from tests.services.factories import all_rows, fresh, make_chat

TIKTOK = "https://www.tiktok.com/@marina/video/7300000000000000000"


@dataclass
class FakeLinks:
    clip: FetchedVideo | None = None
    error: PortError | None = None
    urls: list[str] = field(default_factory=list)

    async def fetch(self, url: str) -> FetchedVideo:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        assert self.clip is not None
        return self.clip


@pytest.mark.parametrize(("text", "link"), [
    (f"глянь {TIKTOK}!", TIKTOK),
    ("https://vm.tiktok.com/ZMabc123/ ржу", "https://vm.tiktok.com/ZMabc123/"),
    ("https://instagram.com/reel/C1x2y3/?igsh=x)", "https://instagram.com/reel/C1x2y3/?igsh=x"),
    ("https://youtube.com/shorts/abc123", "https://youtube.com/shorts/abc123"),
    ("https://youtu.be/abc123.", "https://youtu.be/abc123"),
    ("https://example.com/video.mp4", None),
    ("просто текст", None),
])
def test_a_short_video_link_is_found(text: str, link: str | None) -> None:
    assert video_link(text) == link


def test_the_proxy_reaches_yt_dlp() -> None:
    assert ydl_options(Path("/tmp/x"), "socks5://p:1")["proxy"] == "socks5://p:1"
    assert "proxy" not in ydl_options(Path("/tmp/x"), "")


async def test_a_link_becomes_a_pending_clip(db: AsyncSession, deps: Deps, fakes: Fakes,
                                             clock: FixedClock) -> None:
    chat = await make_chat(db)
    channel = await db.get(Channel, chat.channel_id)
    assert channel is not None
    peer = Peer(external_id="777")
    await Inbox(deps).ingest(InboundMessage(
        channel_id=channel.id, peer=peer, external_id="m1", text=f"что скажешь? {TIKTOK}",
        sent_at=clock.now() - timedelta(seconds=1)))
    message = (await all_rows(db, select(Message).where(Message.external_id == "m1")))[0]
    assert message.kind == MessageKind.TEXT and message.media_id is not None
    media = await db.get(MediaAsset, message.media_id)
    assert media is not None and is_link_key(media.storage_key)
    assert (media.kind, media.file_name, media.understanding) == (
        MediaKind.VIDEO, TIKTOK, UnderstandingStatus.PENDING)
    assert ("understand", media.id) in fakes.tasks.calls


async def link_message(db: AsyncSession, clock: FixedClock, chat: Chat | None = None,
                       key: str = "a") -> MediaAsset:
    chat = chat or await make_chat(db)
    media = MediaAsset(persona_id=None, source=MediaSource.INCOMING, kind=MediaKind.VIDEO,
                       storage_key=f"{LINK_KEY_PREFIX}{key}", mime="video/mp4",
                       file_name=TIKTOK,
                       size_bytes=0, sha256="", understanding=UnderstandingStatus.PENDING)
    db.add(media)
    await db.flush()
    db.add(Message(chat_id=chat.id, direction=Direction.IN, author=Author.CLIENT,
                   kind=MessageKind.TEXT, text=f"глянь {TIKTOK}", media_id=media.id,
                   status=MessageStatus.RECEIVED, external_id=f"m-{key}", sent_at=clock.now()))
    await db.commit()
    return media


async def test_the_clip_is_downloaded_seen_and_heard(db: AsyncSession, deps: Deps,
                                                    fakes: Fakes, clock: FixedClock) -> None:
    media = await link_message(db, clock)
    links = FakeLinks(clip=FetchedVideo(data=b"MP4CLIP", mime="video/mp4", title="кот",
                                        duration_s=12.0))
    fakes.transcriber.text = "смотри какой кот"
    fakes.llm.script("vision-model", "Кот прыгает на шкаф и падает.")

    status = await Understanding(replace(deps, links=links)).understand(media.id)

    assert status == UnderstandingStatus.DONE and links.urls == [TIKTOK]
    media = await fresh(db, media)
    assert not is_link_key(media.storage_key) and media.size_bytes == len(b"MP4CLIP")
    assert (media.duration_s, media.caption) == (12.0, "кот")
    assert media.description == "Видно: Кот прыгает на шкаф и падает. Говорит: смотри какой кот"
    assert await fakes.storage.get(media.storage_key) == b"MP4CLIP"
    assert incoming_media(MessageKind.TEXT, media).startswith(f"[{LINK_LABEL}] Видно: Кот")


async def test_a_clip_that_does_not_open(db: AsyncSession, deps: Deps, clock: FixedClock) -> None:
    chat = await make_chat(db)
    media = await link_message(db, clock, chat)
    links = FakeLinks(error=PortError("ролик длинный (900 с)"))

    status = await Understanding(replace(deps, links=links)).understand(media.id)

    assert status == UnderstandingStatus.FAILED
    media = await fresh(db, media)
    assert media.understanding_error == "ролик длинный (900 с)"
    assert media_label(MessageKind.TEXT, media) == LINK_LABEL
    assert incoming_media(MessageKind.TEXT, media) == (
        f"[{LINK_LABEL}: не открылся — ролик длинный (900 с)]")
    # Without a downloader the link stays a link she can not open.
    other = await link_message(db, clock, chat, key="b")
    assert await Understanding(replace(deps, links=None)).understand(other.id) == (
        UnderstandingStatus.FAILED)
