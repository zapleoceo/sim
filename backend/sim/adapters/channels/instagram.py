"""Instagram adapter (private API via instagrapi, polling).

The adapter never touches instagrapi: it maps a thin IGTransport's raw dicts to domain
types. InstagrapiTransport is the only place that does, and imports it lazily."""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sim.adapters.channels._http import download_bounded
from sim.adapters.channels.capabilities import Capability
from sim.adapters.channels.ig_parse import item_content
from sim.domain.enums import ChannelKind, SessionStatus
from sim.ports.channel import InboundMessage, SendResult

logger = logging.getLogger(__name__)

LIVE_THREADS = 20          # newest threads only: each page costs an IG call + 2-5s delay
SEEN_LIMIT = 2000


class IGTransport(Protocol):
    async def fetch_threads(self) -> list[dict[str, Any]]: ...

    async def send_direct(self, thread_id: str, text: str) -> dict[str, Any]: ...

    async def account_health(self) -> str: ...

    async def upload_photo(self, path: Path, caption: str) -> str: ...

    async def download_media(self, url: str) -> bytes: ...


@dataclass
class IGCursor:
    """Item ids already handed out. Ids, not timestamps: a message-request thread that is
    accepted later surfaces items OLDER than any timestamp cursor. Lives as long as the
    object does; the connector keeps one per channel so it survives port rebuilds."""

    seen: OrderedDict[str, None] = field(default_factory=OrderedDict)

    def fresh(self, item_id: str) -> bool:
        if item_id in self.seen:
            return False
        self.seen[item_id] = None
        while len(self.seen) > SEEN_LIMIT:
            self.seen.popitem(last=False)
        return True


def paged_threads(client: Any, endpoint: str, amount: int = LIVE_THREADS) -> list[dict]:
    """Raw inbox threads with cursor pagination (instagrapi's extractor is bypassed: it
    crashes on shared-media items)."""
    out: list[dict] = []
    cursor = None
    for _ in range(max(1, (amount + 19) // 20)):
        params = {"visual_message_return_type": "unseen", "thread_message_limit": "10",
                  "persistentBadging": "true", "limit": "20"}
        if cursor:
            params["cursor"] = cursor
            params["direction"] = "older"
        res = client.private_request(endpoint, params=params)
        inbox = res.get("inbox", {})
        out.extend(inbox.get("threads", []))
        if len(out) >= amount or not inbox.get("has_older"):
            break
        cursor = inbox.get("oldest_cursor")
        if not cursor:
            break
    return out[:amount]


class InstagrapiTransport:
    def __init__(self, *, client_factory: Callable[[], Any], own_user_id: str = "") -> None:
        self._factory = client_factory
        self._own_id = own_user_id
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = self._factory()
        return self._client

    def _resolve_own_id(self, client: Any) -> str:
        """Our numeric IG id. REQUIRED to tell our own items from the lead's; fail CLOSED:
        a blank id would label every item inbound (prod incident in stepan2: 1401 own
        sends filed as client messages)."""
        own = self._own_id
        if not own:
            auth = getattr(client, "authorization_data", None) or {}
            own = str(auth.get("ds_user_id") or getattr(client, "user_id", "") or "")
        if not own:
            raise RuntimeError("cannot resolve own IG user id; skipping poll")
        return own

    async def fetch_threads(self) -> list[dict[str, Any]]:
        client = self._ensure_client()
        own_id = self._resolve_own_id(client)
        out: list[dict[str, Any]] = []
        seen_threads: set[str] = set()
        # inbox = accepted chats; pending_inbox = message requests (cold leads).
        for endpoint in ("direct_v2/inbox/", "direct_v2/pending_inbox/"):
            try:
                threads = await asyncio.to_thread(paged_threads, client, endpoint)
            except Exception as exc:
                if _is_auth_error(exc):
                    raise
                logger.warning("IG %s failed: %s", endpoint, type(exc).__name__)
                continue
            for t in threads:
                items = t.get("items") or []
                tid = str(t.get("thread_id", ""))
                if t.get("is_group") or not items or not tid or tid in seen_threads:
                    continue
                seen_threads.add(tid)
                lead = next(
                    (u for u in (t.get("users") or []) if str(u.get("pk", "")) != own_id), None
                ) or {}
                base = {
                    "thread_id": tid,
                    "lead_id": str(lead.get("pk") or "") or None,
                    "username": lead.get("username") or None,
                    "full_name": lead.get("full_name") or None,
                    "avatar": str(lead.get("profile_pic_url") or "") or None,
                }
                for item in reversed(items):          # oldest first
                    content = item_content(item)
                    uid = str(item.get("user_id", ""))
                    if content is None or not uid:    # unknown sender: never guess a side
                        continue
                    out.append({
                        **base,
                        "item_id": str(item.get("item_id") or item.get("client_context") or "")
                        or None,
                        "from_me": uid == own_id,
                        "sender_id": uid,
                        "timestamp": item.get("timestamp"),
                        **content,
                    })
        return out

    async def send_direct(self, thread_id: str, text: str) -> dict[str, Any]:
        client = self._ensure_client()
        message = await asyncio.to_thread(client.direct_send, text, thread_ids=[int(thread_id)])
        return {"item_id": str(message.id)}

    async def account_health(self) -> str:
        client = self._ensure_client()
        try:
            await asyncio.to_thread(client.get_timeline_feed)
        except Exception as exc:
            name = type(exc).__name__.lower()
            if "challenge" in name:
                return "challenge"
            if "loginrequired" in name:
                return "expired"
            # A blip (timeout/throttle) must NOT read as expired: that triggers a needless
            # re-login from a datacenter IP, the exact checkpoint path to avoid.
            logger.warning("IG health inconclusive (%s): treating as ok", type(exc).__name__)
        return "ok"

    async def upload_photo(self, path: Path, caption: str) -> str:
        client = self._ensure_client()
        media = await asyncio.to_thread(client.photo_upload, Path(path), caption)
        return str(getattr(media, "pk", "") or getattr(media, "id", ""))

    async def download_media(self, url: str) -> bytes:
        return await download_bounded(url)


def _is_auth_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "challenge" in name or "loginrequired" in name


def ig_dt(value: Any) -> datetime:
    """IG timestamps are epoch microseconds."""
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000, tz=UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(UTC).replace(tzinfo=None)


class InstagramAdapter:
    kind: ChannelKind = ChannelKind.INSTAGRAM
    capabilities = frozenset({Capability.PUBLISH_PHOTO, Capability.DOWNLOAD_MEDIA})

    def __init__(self, transport: IGTransport, *, cursor: IGCursor | None = None) -> None:
        self._t = transport
        self._cursor = cursor if cursor is not None else IGCursor()

    async def fetch_inbound(self) -> list[InboundMessage]:
        out: list[InboundMessage] = []
        for raw in await self._t.fetch_threads():
            iid = raw.get("item_id") or f"{raw['thread_id']}:{raw.get('timestamp')}"
            if not self._cursor.fresh(str(iid)):
                continue
            from_me = bool(raw.get("from_me"))
            lead_id = raw.get("lead_id") or str(raw.get("sender_id", ""))
            uname = raw.get("username")
            out.append(InboundMessage(
                external_thread_id=str(raw["thread_id"]),
                external_user_id=str(lead_id),
                text=str(raw.get("text", "")),
                occurred_at=ig_dt(raw.get("timestamp")),
                external_id=raw.get("item_id") or None,
                user_name=raw.get("full_name"),
                user_handle=f"@{uname}" if uname else None,
                avatar_url=raw.get("avatar"),
                media_kind=raw.get("media_kind"),
                media_url=raw.get("media_url"),
                from_me=from_me,
            ))
        return out

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        try:
            raw = await self._t.send_direct(external_thread_id, text)
        except Exception as exc:
            return SendResult(ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return SendResult(ok=True, external_message_id=str(raw.get("item_id") or "") or None)

    async def session_status(self) -> SessionStatus:
        health = await self._t.account_health()
        if health == "ok":
            return SessionStatus.ACTIVE
        return SessionStatus.CHALLENGE if health == "challenge" else SessionStatus.EXPIRED

    async def publish_photo(self, path: Path, caption: str) -> str:
        """Capability PUBLISH_PHOTO: upload a feed photo, returns the media id."""
        return await self._t.upload_photo(path, caption)

    async def download_media(self, ref: str) -> bytes:
        if not ref.startswith("https://"):
            raise ValueError("instagram media ref must be an https url")
        return await self._t.download_media(ref)
