"""Offline-only photo publication tests."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from sim.adapters.channels.capabilities import Capability
from sim.adapters.db.models import Channel, ContentPost, Persona
from sim.domain.enums import ChannelKind, ContentKind, ContentStatus, SessionStatus


@pytest.fixture
async def photo(container, world):
    calls, builds = [], []
    port = SimpleNamespace(capabilities={Capability.PUBLISH_PHOTO}, result="media-1")

    async def publish(path, caption):
        calls.append((path, caption))
        if isinstance(port.result, Exception):
            raise port.result
        return port.result

    def build(channel_id, session):
        builds.append(channel_id)
        return port

    port.publish_photo = publish
    container.registry = {ChannelKind.INSTAGRAM: SimpleNamespace(build_port=build)}
    container.settings.ig_content_enabled = True
    name = container.media.save(b"\xff\xd8\xffphoto", "jpg")
    async with container.factory() as s, s.begin():
        channel = Channel(persona_id=world.persona.id, kind=ChannelKind.INSTAGRAM,
                          status=SessionStatus.ACTIVE,
                          session_enc=container.box.encrypt_json({"fake": True}))
        s.add(channel)
        await s.flush()
        post = ContentPost(persona_id=world.persona.id, channel_id=channel.id,
                           kind=ContentKind.PHOTO, caption="Approved caption",
                           media_urls=[f"/api/media/{name}"], status=ContentStatus.APPROVED)
        s.add(post)
        await s.flush()
        ids = post.id, channel.id
    container.settings.ig_content_post_ids = (ids[0],)
    return SimpleNamespace(id=ids[0], channel=ids[1], persona=world.persona.id,
                           port=port, calls=calls, builds=builds, name=name)


async def run(container, photo):
    async with container.factory() as s, s.begin():
        await container.content(s).publish(photo.id)
    async with container.factory() as s:
        post = await s.get(ContentPost, photo.id)
        return post.status, post.error


async def test_success_once(container, photo):
    assert await run(container, photo) == (ContentStatus.PUBLISHED, None)
    await run(container, photo)
    assert len(photo.calls) == 1 and photo.calls[0][1] == "Approved caption"
    assert photo.builds == [photo.channel]


@pytest.mark.parametrize("gate", ["disabled", "empty", "other"])
async def test_gates(container, photo, gate):
    if gate == "disabled":
        container.settings.ig_content_enabled = False
    else:
        container.settings.ig_content_post_ids = () if gate == "empty" else (999999,)
    assert (await run(container, photo))[0] == ContentStatus.FAILED
    assert not photo.builds


@pytest.mark.parametrize("changes", [
    {"kind": ContentKind.VIDEO}, {"kind": ContentKind.REEL}, {"kind": ContentKind.STORY},
    {"media_urls": []}, {"media_urls": ["/api/media/abcdefgh.jpg"] * 2},
    {"media_urls": ["https://127.0.0.1/secret.jpg"]}, {"media_urls": ["/etc/passwd"]},
    {"media_urls": ["/api/media/../secret.jpg"]},
    {"media_urls": ["/api/media/%2e%2e%2fsecret.jpg"]},
    {"media_urls": ["/api/media/abcdefgh.jpg?token=secret"]},
    {"media_urls": ["/api/media/abcdefgh.jpg#secret"]},
    {"media_urls": ["/api/media/abcdefgh.png"]},
    {"media_urls": ["/api/media/missing1.jpg"]}, {"channel_id": None},
])
async def test_invalid_posts(container, photo, changes):
    async with container.factory() as s, s.begin():
        post = await s.get(ContentPost, photo.id)
        for key, value in changes.items():
            setattr(post, key, value)
    assert (await run(container, photo))[0] == ContentStatus.FAILED
    assert not photo.builds


@pytest.mark.parametrize("changes", [
    {"kind": ChannelKind.TELEGRAM}, {"is_archived": True},
    {"status": SessionStatus.EXPIRED}, {"session_enc": None},
    {"session_enc": "secret invalid session"},
])
async def test_invalid_channels(container, photo, changes):
    async with container.factory() as s, s.begin():
        channel = await s.get(Channel, photo.channel)
        for key, value in changes.items():
            setattr(channel, key, value)
    status, error = await run(container, photo)
    assert status == ContentStatus.FAILED and "secret" not in error
    assert not photo.builds


@pytest.mark.parametrize("result", ["", "   ", None, RuntimeError("https://secret:token@host")])
async def test_uncertain_not_retried(container, photo, result):
    photo.port.result = result
    status, error = await run(container, photo)
    assert status == ContentStatus.FAILED and "uncertain" in error and "secret" not in error
    await run(container, photo)
    assert len(photo.calls) == 1


@pytest.mark.parametrize("mode", ["symlink", "wrong_bytes"])
async def test_unsafe_local_media(container, photo, tmp_path, mode):
    path = container.media.root / photo.name
    if mode == "symlink":
        path.unlink()
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(b"\xff\xd8\xffsecret")
        path.symlink_to(outside)
    else:
        path.write_bytes(b"not JPEG")
    assert (await run(container, photo))[0] == ContentStatus.FAILED
    assert not photo.builds


@pytest.mark.parametrize("status", [ContentStatus.DRAFT, ContentStatus.FAILED, ContentStatus.PUBLISHED])
async def test_unapproved_not_sent(container, photo, status):
    async with container.factory() as s, s.begin():
        (await s.get(ContentPost, photo.id)).status = status
    assert (await run(container, photo))[0] == status
    assert not photo.builds


async def test_future_not_sent(container, photo):
    async with container.factory() as s, s.begin():
        post = await s.get(ContentPost, photo.id)
        post.status = ContentStatus.SCHEDULED
        post.scheduled_at = container.clock() + timedelta(days=1)
    assert (await run(container, photo))[0] == ContentStatus.SCHEDULED
    assert not photo.builds


async def test_inactive_persona(container, photo):
    async with container.factory() as s, s.begin():
        (await s.get(Persona, photo.persona)).is_active = False
    assert (await run(container, photo))[0] == ContentStatus.FAILED
    assert not photo.builds


@pytest.mark.parametrize("action", ["update", "approve", "publish"])
async def test_stale_orm_cannot_reset_published(container, photo, action):
    from sqlalchemy import update

    from sim.services.content import ContentError

    async with container.factory() as s, s.begin():
        stale = await s.get(ContentPost, photo.id)
        await s.execute(update(ContentPost).where(ContentPost.id == photo.id)
                        .values(status=ContentStatus.PUBLISHED)
                        .execution_options(synchronize_session=False))
        assert stale.status == ContentStatus.APPROVED
        service = container.content(s)
        if action == "publish":
            await service.publish(photo.id)
        else:
            with pytest.raises(ContentError, match="published"):
                if action == "update":
                    await service.update(stale, by_owner=True, caption="Changed")
                else:
                    await service.approve(stale)
        assert stale.status == ContentStatus.PUBLISHED
    assert not photo.builds


async def test_missing_media_identifier_is_empty():
    from sim.adapters.channels.instagram import InstagrapiTransport

    client = SimpleNamespace(photo_upload=lambda *_: SimpleNamespace(pk=None, id=None))
    transport = InstagrapiTransport(client_factory=lambda: client)
    from pathlib import Path

    assert await transport.upload_photo(Path("fake.jpg"), "caption") == ""


@pytest.mark.parametrize("case", ["wrong_persona", "missing_connector", "missing_capability"])
async def test_configuration_failures(container, photo, case):
    if case == "wrong_persona":
        async with container.factory() as s, s.begin():
            other = Persona(name="Other persona")
            s.add(other)
            await s.flush()
            (await s.get(Channel, photo.channel)).persona_id = other.id
    elif case == "missing_connector":
        container.registry = {}
    else:
        photo.port.capabilities = set()
    assert (await run(container, photo))[0] == ContentStatus.FAILED
    assert not photo.calls
