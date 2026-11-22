"""Links to short videos a client sends — a TikTok, an Instagram reel, a YouTube short
(docs/reply.md, «Ролик по ссылке»). The link becomes a video she watches: the inbox gives the
text message a pending placeholder asset (`LINK_KEY_PREFIX`), understanding downloads the clip
and describes it like a sent video. Pure."""
from __future__ import annotations

import re

LINK_KEY_PREFIX = "link:"           # media_asset.storage_key of a clip not downloaded yet
LINK_FILE_MAX = 255                 # media_asset.file_name keeps the link
MAX_CLIP_S = 240                    # longer clips are not downloaded
MAX_CLIP_BYTES = 60 * 1024 * 1024

VIDEO_LINK = re.compile(
    r"https?://(?:www\.|m\.|vm\.|vt\.)?"
    r"(?:tiktok\.com/\S+"
    r"|instagram\.com/(?:reel|reels|p|tv)/\S+"
    r"|youtube\.com/shorts/\S+"
    r"|youtu\.be/\S+)",
    re.IGNORECASE)
TRAILING = ".,;:!?)»\"'"


def video_link(text: str) -> str | None:
    """The first short-video link in a message, without trailing punctuation."""
    found = VIDEO_LINK.search(text or "")
    if found is None:
        return None
    return found.group(0).rstrip(TRAILING)


def is_link_key(storage_key: str) -> bool:
    return storage_key.startswith(LINK_KEY_PREFIX)
