"""Raw Direct threads of the private API -> items SIM can ingest. Pure, no instagrapi.

instagrapi's own extractor crashes on shared media (a shared post / reel item carries an
`instagram://` URL that fails its validation) and would take the whole poll down, so the poll
reads the raw JSON of `direct_v2/inbox/` (and of `direct_v2/pending_inbox/` while message
requests wait) and this module turns
every item into text + an optional media URL. Nothing is silently lost: a shared post, reel,
story or profile becomes a captioned line, real media in the DM gets its CDN URL.

Sides are decided by the item's `user_id` against our own id. An item without `user_id` is
skipped — never guessed (stepan2: a blank own id filed 1401 own sends as client messages), and
the poll refuses to run without our own id at all (fail closed)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

LIVE_THREADS = 20           # newest threads per box: each page is an API call + 2-5 s delay
THREAD_MESSAGES = 10        # newest items per thread
INBOX, PENDING = "direct_v2/inbox/", "direct_v2/pending_inbox/"   # chats, message requests

# item_type values that are system events, not messages.
SKIP_TYPES = frozenset({"action_log", "video_call_event", "placeholder"})
# A line for an item with no text of its own.
PLACEHOLDERS = {
    "xma_link": "🔗 ссылка", "generic_xma": "📎 вложение", "link": "🔗 ссылка",
    "xma_media_share": "📷 пост", "media_share": "📷 пост", "xma_clip": "🎬 reels",
    "clip": "🎬 reels", "xma_reel_share": "🎬 reels", "xma_story_share": "📖 сторис",
    "story_share": "📖 сторис", "reel_share": "📖 ответ на сторис",
    "xma_profile_share": "👤 профиль", "profile": "👤 профиль",
    "xma_product_share": "🛍 товар", "media": "🖼 фото", "raven_media": "🖼 фото",
    "animated_media": "GIF", "voice_media": "🎤 голосовое", "store_sticker": "🏷 стикер",
    "like": "❤️",
}
XMA_EMOJI = {
    "xma_link": "🔗", "generic_xma": "📎", "xma_media_share": "📷", "xma_clip": "🎬",
    "xma_reel_share": "🎬", "xma_story_share": "📖", "xma_profile_share": "👤",
    "xma_product_share": "🛍",
}
URL = re.compile(r"https?://[^\s]+")


@dataclass(frozen=True)
class IgItem:
    """One message of a Direct thread, oldest first in a poll."""

    thread_id: str
    item_id: str
    from_me: bool
    user_id: str                    # the other person (the client) of the thread
    username: str | None
    full_name: str
    text: str
    at: datetime
    media_kind: str | None = None   # photo | video | voice
    media_url: str | None = None
    reply_to: str | None = None     # the item this one replies to


def canonical_media(url: str, item_type: str | None = None) -> str:
    """instagram.com/{reel|reels|p|tv}/{code}/… -> the native permalink (a sponsored clip shares
    a POST under a /reel/ URL, so the type comes from the item, not the path)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.netloc.endswith("instagram.com"):
        return url
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("reel", "reels", "p", "tv"):
        kind = "reel" if item_type in ("xma_clip", "xma_reel_share", "clip") else "p"
        return f"https://www.instagram.com/{kind}/{parts[1]}/"
    return url


def clean_url(url: Any, item_type: str | None = None) -> str | None:
    """Unwrap l.instagram.com/?u=… and canonicalize Instagram media links."""
    value = str(url or "").strip()
    if not value:
        return None
    if "l.instagram.com" in value and "u=" in value:
        try:
            real = parse_qs(urlparse(value).query).get("u", [None])[0]
        except ValueError:
            real = None
        if real:
            value = unquote(real)
    return canonical_media(value, item_type) or None


def _payload(item: dict[str, Any]) -> dict[str, Any] | None:
    item_type = str(item.get("item_type") or "")
    payload = item.get(item_type)
    if payload is None and item_type.startswith("xma_"):
        payload = item.get(item_type[4:])
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    return payload if isinstance(payload, dict) else None


def _card_text(payload: dict[str, Any]) -> str | None:
    parts = [str(payload[key]).strip() for key in (
        "header_title_text", "header_subtitle_text", "title_text", "subtitle_text",
        "caption_body_text") if str(payload.get(key) or "").strip()]
    context = payload.get("link_context")
    if not parts and isinstance(context, dict):
        parts = [str(context[key]).strip() for key in ("link_title", "link_summary")
                 if str(context.get(key) or "").strip()]
    return " · ".join(dict.fromkeys(parts)) or None


def _versions_url(media: dict[str, Any]) -> tuple[str, str] | None:
    videos = media.get("video_versions")
    if videos:
        return "video", str(videos[0].get("url") or "")
    candidates = (media.get("image_versions2") or {}).get("candidates") or []
    if candidates:
        return "photo", str(candidates[0].get("url") or "")
    return None


def media_of(item: dict[str, Any]) -> tuple[str, str] | None:
    """(kind, url) of real media sent in the DM (photo, video, GIF, voice). A shared post is
    not media here: its card becomes text."""
    item_type = item.get("item_type")
    if item_type in ("media", "raven_media"):
        # A view-once photo keeps its media under visual_media.media, not raven_media.
        found = [item.get(item_type) or {}, (item.get("visual_media") or {}).get("media") or {}]
        found += [base["media"] for base in list(found) if isinstance(base.get("media"), dict)]
        for media in found:
            got = _versions_url(media)
            if got and got[1]:
                return got
    elif item_type == "animated_media":
        images = (item.get("animated_media") or {}).get("images") or {}
        for key in ("fixed_height", "original", "fixed_width"):
            url = (images.get(key) or {}).get("url")
            if url:
                return "photo", str(url)
    elif item_type == "voice_media":
        audio = ((item.get("voice_media") or {}).get("media") or {}).get("audio") or {}
        if audio.get("audio_src"):
            return "voice", str(audio["audio_src"])
    return None


def item_text(item: dict[str, Any]) -> str | None:
    """The text of an item (its own, a shared card's, or a placeholder); None for system
    events and empty text items."""
    item_type = str(item.get("item_type") or "")
    if item_type in SKIP_TYPES:
        return None
    text = str(item.get("text") or "").strip()
    payload = _payload(item)
    link = None
    if payload:
        context = payload.get("link_context")
        context = context if isinstance(context, dict) else {}
        link = clean_url(payload.get("target_url") or context.get("link_url"), item_type)
        if not text:
            text = str(payload.get("text") or "").strip()
    if not text:
        card = _card_text(payload) if payload else None
        if card:
            text = f"{XMA_EMOJI.get(item_type, '📎')} {card}"
        elif link and item_type in ("link", "xma_link"):
            text = f"🔗 {link}"
        elif item_type == "text":
            return None
        else:
            text = PLACEHOLDERS.get(item_type, f"[{item_type}]")
    if link and link not in text and item_type not in ("media", "raven_media"):
        text = f"{text}\n{link}"
    return text


def ig_time(value: Any) -> datetime:
    """Item timestamps are epoch microseconds."""
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000, tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(UTC)


def thread_items(threads: list[dict[str, Any]], own_id: str) -> list[IgItem]:
    """Every item of these one-to-one threads, oldest first; groups, empty threads, items
    without a sender and duplicates (a thread in both boxes) are skipped."""
    if not own_id:
        raise ValueError("own Instagram id is unknown: refusing to tell sides apart")
    out: list[IgItem] = []
    seen: set[str] = set()
    for thread in threads:
        thread_id = str(thread.get("thread_id") or "")
        items = thread.get("items") or []
        if thread.get("is_group") or not items or not thread_id or thread_id in seen:
            continue
        seen.add(thread_id)
        other = next((u for u in thread.get("users") or []
                      if str(u.get("pk") or u.get("pk_id") or "") != own_id), None) or {}
        other_id = str(other.get("pk") or other.get("pk_id") or "")
        for item in items:
            sender = str(item.get("user_id") or "")
            item_id = str(item.get("item_id") or "")
            text = item_text(item)
            if text is None or not sender or not item_id:
                continue
            media = media_of(item)
            replied = item.get("replied_to_message") or {}
            out.append(IgItem(
                thread_id=thread_id, item_id=item_id, from_me=sender == own_id,
                user_id=other_id or sender, username=other.get("username") or None,
                full_name=str(other.get("full_name") or ""), text=text,
                at=ig_time(item.get("timestamp")),
                media_kind=media[0] if media else None, media_url=media[1] if media else None,
                reply_to=str(replied.get("item_id")) if replied.get("item_id") else None,
            ))
    out.sort(key=lambda found: found.at)
    return out


@dataclass(frozen=True)
class Box:
    """The newest raw threads of one box, and how many message requests wait: the chats'
    answer carries `pending_requests_total` (None when Instagram did not say)."""

    threads: list[dict[str, Any]]
    pending: int | None = None


def read_box(client: Any, endpoint: str, amount: int = LIVE_THREADS) -> Box:
    """The newest `amount` raw threads of one box (blocking: run it in a thread)."""
    threads: list[dict[str, Any]] = []
    pending: int | None = None
    cursor = None
    for page in range(max(1, (amount + 19) // 20)):
        params = {"visual_message_return_type": "unseen", "persistentBadging": "true",
                  "thread_message_limit": str(THREAD_MESSAGES), "limit": "20"}
        if cursor:
            params.update(cursor=cursor, direction="older")
        answer = client.private_request(endpoint, params=params) or {}
        if page == 0:
            pending = _count(answer.get("pending_requests_total"))
        inbox = answer.get("inbox") or {}
        threads.extend(inbox.get("threads") or [])
        cursor = inbox.get("oldest_cursor")
        if len(threads) >= amount or not inbox.get("has_older") or not cursor:
            break
    return Box(threads[:amount], pending)


def _count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def media_text(text: str, media_kind: str | None) -> str:
    """A photo / voice item's placeholder is not a caption: the attachment says it."""
    if media_kind and text in PLACEHOLDERS.values():
        return ""
    return text
