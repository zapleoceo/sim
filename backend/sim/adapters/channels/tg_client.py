"""Single Telethon client factory: StringSession, proxy and anti-ban client flags in ONE place.

The client acts as a quiet second device of a personal account, not a bot:
- receive_updates=False: no update stream is requested, so the account is not flagged as an
  actively listening client and nothing marks messages as read on our side;
- flood_sleep_threshold=0: Telethon would otherwise sleep through short FloodWaits inside any
  call; we surface them (FloodWaitError) so the caller backs off visibly."""
from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit

_PROXY_TYPES = {"socks5": "socks5", "socks5h": "socks5", "socks4": "socks4", "http": "http",
                "https": "http"}


def parse_proxy(url: str) -> dict[str, Any] | None:
    """`scheme://user:pass@host:port` (scheme defaults to socks5) -> Telethon proxy dict."""
    raw = (url or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw if "://" in raw else f"socks5://{raw}")
    kind = _PROXY_TYPES.get(parts.scheme.lower())
    try:
        port = parts.port
    except ValueError:
        port = None
    if kind is None or not parts.hostname or not port:
        raise ValueError("proxy must look like socks5://user:pass@host:port")
    proxy: dict[str, Any] = {"proxy_type": kind, "addr": parts.hostname, "port": port,
                             "rdns": True}
    if parts.username:
        proxy["username"] = unquote(parts.username)
        proxy["password"] = unquote(parts.password or "")
    return proxy


def build_tg_client(string_session: str, api_id: int, api_hash: str, proxy: str = "") -> Any:
    from telethon import TelegramClient  # noqa: PLC0415
    from telethon.sessions import StringSession  # noqa: PLC0415

    return TelegramClient(
        StringSession(string_session or None), api_id, api_hash,
        proxy=parse_proxy(proxy), receive_updates=False, flood_sleep_threshold=0,
        connection_retries=3, request_retries=3,
    )
