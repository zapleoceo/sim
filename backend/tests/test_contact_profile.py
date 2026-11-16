"""Synthetic current-contact reads: no Telegram, model inference or channel sends."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sim.adapters.channels.capabilities import Capability
from sim.adapters.channels.telegram_user import ProfilePhotoBuffer, TelegramUserAdapter, TGUserState
from sim.domain.enums import ChannelKind
from sim.services.contact_profile import MAX_PHOTO_BYTES, CurrentContactProfile
from sim.services.safe_payload import safe_payload


def setup_read():
    dialog = SimpleNamespace(id=1, channel_id=10, client_id=20, external_thread_id="12345",
                             client=SimpleNamespace(persona_id=30), channel=SimpleNamespace(
                                 kind=ChannelKind.TELEGRAM_USER, is_archived=False, persona_id=30))
    port = SimpleNamespace(capabilities={Capability.READ_CONTACT_PROFILE},
                           read_contact_profile=AsyncMock(return_value={
                               "name": "Test", "username": "known_user", "bio": "Biography",
                               "has_photo": True, "phone": "must-not-pass", "access_hash": "hidden"}),
                           read_contact_photo=AsyncMock(return_value=b"\xff\xd8\xffsynthetic"))
    catalog = [{"id": "selected-model", "available": True,
                "job_request": {"message_content": ["text", "image_data_url"]}}]
    llm = SimpleNamespace(llm=SimpleNamespace(list_models=AsyncMock(return_value=catalog)),
                          tracer=SimpleNamespace(record=AsyncMock()))
    service = CurrentContactProfile(SimpleNamespace(scalar=AsyncMock(return_value=1)), llm,
                                    clock=lambda: datetime(2026, 11, 16, 18, 0))
    return service, dialog, port, llm


async def test_profile_photo_scoped_and_audited_without_payload():
    service, dialog, port, llm = setup_read()
    result = await service.read(dialog, port, "В профиле посмотри", channel_id=10, model="selected-model")
    assert '"username": "known_user"' in result.text and "Biography" in result.text
    assert "2026-11-16T18:00:00Z" in result.text and "one_photo_attached" in result.text
    assert result.photo.startswith("data:image/jpeg;base64,")
    assert "must-not-pass" not in result.text and "hidden" not in result.text
    port.read_contact_profile.assert_awaited_once_with("12345")
    port.read_contact_photo.assert_awaited_once_with("12345")
    assert llm.tracer.record.call_args.kwargs["detail"] == {
        "operation": "current_contact_profile", "status": "available", "photo_attached": True}
    assert "data:" not in str(safe_payload({"content": result.photo}))


@pytest.mark.parametrize("case", ["disabled", "other_account", "other_persona", "other_contact", "group", "archived"])
async def test_scope_failures_never_reach_telegram(case):
    service, dialog, port, _ = setup_read()
    channel_id = 10
    if case == "disabled":
        channel_id = 0
    elif case == "other_account":
        channel_id = 11
    elif case == "other_persona":
        dialog.channel.persona_id = 99
    elif case == "other_contact":
        service.session.scalar.return_value = None
    elif case == "group":
        dialog.external_thread_id = "-12345"
    else:
        dialog.channel.is_archived = True
    result = await service.read(dialog, port, "Посмотри профиль", channel_id=channel_id, model="selected-model")
    assert "not_enabled" in result.text and not result.photo
    port.read_contact_profile.assert_not_awaited()
    port.read_contact_photo.assert_not_awaited()


async def test_normal_conversation_does_not_read_profile_or_catalog():
    service, dialog, port, llm = setup_read()
    result = await service.read(dialog, port, "Привет", channel_id=10, model="selected-model")
    assert result.text == result.photo == ""
    port.read_contact_profile.assert_not_awaited()
    llm.llm.list_models.assert_not_awaited()


@pytest.mark.parametrize("error", [ValueError("private"), TimeoutError("private")])
async def test_unavailable_profile_does_not_leak_error(error):
    service, dialog, port, _ = setup_read()
    port.read_contact_profile.side_effect = error
    result = await service.read(dialog, port, "my profile", channel_id=10, model="selected-model")
    assert '"status": "unavailable"' in result.text and "private" not in result.text
    assert not result.photo


async def test_missing_username_and_photo_are_not_invented():
    service, dialog, port, _ = setup_read()
    port.read_contact_profile.return_value = {"name": "Test", "has_photo": False}
    result = await service.read(dialog, port, "avatar", channel_id=10, model="selected-model")
    assert '"username": ""' in result.text and '"photo_status": "not_available"' in result.text
    port.read_contact_photo.assert_not_awaited()


async def test_photo_failure_keeps_successfully_read_profile_fields():
    service, dialog, port, _ = setup_read()
    port.read_contact_photo.side_effect = ValueError("private photo")
    result = await service.read(dialog, port, "profile", channel_id=10, model="selected-model")
    assert '"status": "available"' in result.text and '"photo_status": "unavailable"' in result.text
    assert '"username": "known_user"' in result.text and "Biography" in result.text
    assert not result.photo and "private photo" not in result.text


async def test_no_hidden_model_fallback():
    service, dialog, port, llm = setup_read()
    llm.llm.list_models.return_value = []
    result = await service.read(dialog, port, "profile", channel_id=10, model="selected-model")
    assert "selected_model_has_no_confirmed_vision" in result.text and not result.photo
    port.read_contact_photo.assert_not_awaited()


@pytest.mark.parametrize("image", [b"not-image", b"\xff\xd8\xff" + b"x" * MAX_PHOTO_BYTES])
async def test_invalid_or_large_photo_never_enters_model_context(image):
    service, dialog, port, _ = setup_read()
    port.read_contact_photo.return_value = image
    result = await service.read(dialog, port, "profile", channel_id=10, model="selected-model")
    assert not result.photo and '"photo_status": "not_available"' in result.text


def test_photo_sink_limits_memory_during_download():
    sink = ProfilePhotoBuffer()
    sink.write(b"x" * MAX_PHOTO_BYTES)
    with pytest.raises(ValueError, match="limit"):
        sink.write(b"x")
    assert len(sink.getvalue()) == MAX_PHOTO_BYTES


async def test_adapter_rejects_group_and_arbitrary_username():
    transport = SimpleNamespace(profile=AsyncMock(), profile_photo=AsyncMock())
    adapter = TelegramUserAdapter(transport, TGUserState())
    with pytest.raises(ValueError):
        await adapter.read_contact_profile("-123")
    with pytest.raises(ValueError):
        await adapter.read_contact_photo("@another_user")
    transport.profile.assert_not_awaited()
    transport.profile_photo.assert_not_awaited()


@pytest.mark.parametrize("flags", [{}, {"bot": True}, {"deleted": True}, {"is_self": True}])
async def test_telethon_profile_projects_only_current_human_and_never_marks_read(flags):
    from telethon import functions, types

    from sim.adapters.channels.telegram_user import TelethonTransport

    calls = []

    class Client:
        def is_connected(self):
            return True

        async def get_input_entity(self, peer):
            assert peer == 12345
            return types.InputUser(peer, 123)

        async def __call__(self, request):
            assert isinstance(request, functions.users.GetFullUserRequest)
            calls.append(request)
            user = types.User(12345, first_name="Test", username="known_user", phone="excluded", **flags)
            return SimpleNamespace(users=[user], full_user=SimpleNamespace(about="test bio"))

    transport = TelethonTransport(Client)
    if flags:
        with pytest.raises(ValueError, match="unavailable"):
            await transport.profile(12345)
    else:
        assert await transport.profile(12345) == {
            "name": "Test", "username": "known_user", "bio": "test bio", "has_photo": False}
    assert len(calls) == 1


async def test_profile_photo_transport_requests_only_small_current_avatar():
    from sim.adapters.channels.telegram_user import TelethonTransport

    class Client:
        def is_connected(self):
            return True

        async def get_input_entity(self, peer):
            return peer

        async def download_profile_photo(self, peer, *, file, download_big):
            assert peer == 12345 and download_big is False
            file.write(b"\xff\xd8\xffsynthetic")

    assert await TelethonTransport(Client).profile_photo(12345) == b"\xff\xd8\xffsynthetic"


async def test_real_encoder_keeps_disclosure_pixels_in_round_visible_area(tmp_path):
    # CI only, local synthetic color input; no provider, Telegram or production data.
    from sim.adapters.audio import run_process
    from sim.adapters.video import VideoNoteEncoder

    source = tmp_path / "synthetic.mp4"
    code, _, _ = await run_process(("ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                                   "-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.2",
                                   "-c:v", "libx264", str(source)), b"", 15)
    assert code == 0
    encoded = await VideoNoteEncoder().to_video_note(source.read_bytes())
    output = tmp_path / "labelled.mp4"
    output.write_bytes(encoded)
    code, pixels, _ = await run_process(("ffmpeg", "-hide_banner", "-loglevel", "error",
                                        "-i", str(output), "-frames:v", "1", "-vf",
                                        "crop=180:40:102:270", "-f", "rawvideo",
                                        "-pix_fmt", "rgb24", "pipe:1"), b"", 15)
    assert code == 0 and len(pixels) == 180 * 40 * 3
    assert sum(1 for i in range(0, len(pixels), 3)
               if min(pixels[i:i + 3]) > 180) > 30  # white glyphs on red source
