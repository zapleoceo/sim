"""Gallery media sending: photo and video_note through the outbound service."""
from __future__ import annotations

from sim.adapters.channels.capabilities import Capability
from sim.domain.enums import MediaKind


def test_send_photo_and_video_note_capabilities_exist():
    assert Capability.SEND_PHOTO == "send_photo"
    assert Capability.SEND_VIDEO_NOTE == "send_video_note"


def test_media_kind_enum():
    assert MediaKind.PHOTO == "photo"
    assert MediaKind.VIDEO == "video"
    assert MediaKind.VIDEO_NOTE == "video_note"
    assert set(MediaKind) == {"photo", "video", "video_note"}


def test_telegram_user_adapter_has_gallery_capabilities():
    from sim.adapters.channels.telegram_user import TelegramUserAdapter
    assert Capability.SEND_PHOTO in TelegramUserAdapter.capabilities
    assert Capability.SEND_VIDEO_NOTE in TelegramUserAdapter.capabilities
