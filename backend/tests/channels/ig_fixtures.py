"""Raw private-API Direct threads, shaped like `direct_v2/inbox/` answers (trimmed)."""
from __future__ import annotations

from typing import Any

OWN = "4242"
LEAD = "1001"
NOW_US = 1_763_750_000_000_000          # 2025-11-21 18:33:20 UTC, epoch microseconds


def us(minutes_ago: float) -> int:
    return NOW_US - int(minutes_ago * 60 * 1_000_000)


def thread(thread_id: str, items: list[dict[str, Any]], *,
           users: list[dict[str, Any]] | None = None, group: bool = False) -> dict[str, Any]:
    return {"thread_id": thread_id, "is_group": group,
            "users": users if users is not None else [
                {"pk": int(LEAD), "username": "dima", "full_name": "Дима Петров"}],
            "items": items}     # newest first, as Instagram sends them


def text(item_id: str, sender: str, body: str, minutes_ago: float) -> dict[str, Any]:
    return {"item_id": item_id, "user_id": int(sender), "item_type": "text", "text": body,
            "timestamp": us(minutes_ago)}


SHARED_REEL: dict[str, Any] = {
    # instagrapi's extractor fails on this item (an instagram:// URL in the shared clip).
    "item_id": "i-reel", "user_id": int(LEAD), "item_type": "xma_clip", "timestamp": us(4),
    "xma_clip": [{"target_url": "https://www.instagram.com/reel/ABC123/?igsh=x",
                  "title_text": "смешное видео", "preview_url": "instagram://media?id=1"}],
}
PHOTO: dict[str, Any] = {
    "item_id": "i-photo", "user_id": int(LEAD), "item_type": "media", "timestamp": us(3),
    "media": {"image_versions2": {"candidates": [{"url": "https://cdn.example/p.jpg"}]}},
}
VIEW_ONCE: dict[str, Any] = {
    "item_id": "i-raven", "user_id": int(LEAD), "item_type": "raven_media", "timestamp": us(2),
    "raven_media": {}, "visual_media": {"media": {"image_versions2": {
        "candidates": [{"url": "https://cdn.example/once.jpg"}]}}},
}
VOICE: dict[str, Any] = {
    "item_id": "i-voice", "user_id": int(LEAD), "item_type": "voice_media", "timestamp": us(1),
    "voice_media": {"media": {"audio": {"audio_src": "https://cdn.example/v.m4a"}}},
}
STORY_REPLY: dict[str, Any] = {
    "item_id": "i-story", "user_id": int(LEAD), "item_type": "reel_share", "timestamp": us(5),
    "text": "ого, где это?", "reel_share": {"text": "ого, где это?", "type": "reply"},
}
NO_SENDER: dict[str, Any] = {"item_id": "i-ghost", "item_type": "text", "text": "кто я?",
                             "timestamp": us(1)}
SYSTEM: dict[str, Any] = {"item_id": "i-sys", "user_id": int(LEAD), "item_type": "action_log",
                          "timestamp": us(1)}

INBOX: list[dict[str, Any]] = [
    thread("t-1", [VOICE, VIEW_ONCE, PHOTO, SHARED_REEL, text("i-mine", OWN, "привет)", 6),
                   STORY_REPLY, text("i-old", LEAD, "давно", 60 * 48), NO_SENDER, SYSTEM]),
    thread("t-group", [text("i-g", LEAD, "всем", 1)], group=True),
    thread("t-empty", []),
]
PENDING: list[dict[str, Any]] = [
    # A message request: a stranger, and t-1 again (both boxes may list one thread).
    thread("t-2", [text("i-new", "2002", "здравствуйте", 7)],
           users=[{"pk": 2002, "username": "anna", "full_name": "Анна"}]),
    thread("t-1", [text("i-dup", LEAD, "дубль", 1)]),
]
