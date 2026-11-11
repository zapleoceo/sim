"""Telegram connector: Bot API, webhook delivery with a per-channel secret token."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sim.adapters.channels.capabilities import Capability
from sim.adapters.channels.telegram import (
    BotApiTransport,
    TelegramAdapter,
    TelegramError,
    TelegramTransport,
    parse_update,
)
from sim.config import settings
from sim.connectors._common import header_value, new_secret, secret_matches
from sim.connectors.spec import Session
from sim.domain.enums import ChannelKind, SessionStatus
from sim.ports.channel import ChannelPort, ConnectResult, InboundMessage

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105 (header name)
ALLOWED_UPDATES = ["message", "edited_message"]


def webhook_url(channel_id: int) -> str:
    return f"{settings().public_url.rstrip('/')}/hooks/telegram/{channel_id}"


class TelegramConnector:
    kind = ChannelKind.TELEGRAM
    label = "Telegram"
    polling = False
    capabilities = frozenset({Capability.DOWNLOAD_MEDIA, Capability.SEND_VOICE})

    def __init__(
        self, transport_factory: Callable[[str], TelegramTransport] = BotApiTransport
    ) -> None:
        self._factory = transport_factory

    async def connect(
        self, channel_id: int, credentials: Mapping[str, str], session: Session
    ) -> ConnectResult:
        token = str(credentials.get("bot_token", "")).strip()
        if not token:
            return ConnectResult(SessionStatus.ERROR, message="bot_token is required",
                                 session=dict(session))
        t = self._factory(token)
        try:
            me = await t.call("getMe")
        except TelegramError as exc:
            msg = "Invalid bot token" if exc.code in (401, 404) else str(exc)
            return ConnectResult(SessionStatus.ERROR, message=msg, session=dict(session))
        secret = new_secret()
        try:
            await t.call("setWebhook", {
                "url": webhook_url(channel_id),
                "secret_token": secret,
                "allowed_updates": ALLOWED_UPDATES,
                "drop_pending_updates": False,
            })
        except TelegramError as exc:
            return ConnectResult(SessionStatus.ERROR, message=f"setWebhook failed: {exc}",
                                 session=dict(session))
        username = (me or {}).get("username") or ""
        return ConnectResult(
            SessionStatus.ACTIVE,
            account_label=f"@{username}" if username else None,
            session={"bot_token": token, "webhook_secret": secret, "bot_username": username},
        )

    async def verify(self, channel_id: int, code: str, session: Session) -> ConnectResult:
        return await self.state(channel_id, session)

    async def state(self, channel_id: int, session: Session) -> ConnectResult:
        token = session.get("bot_token")
        if not token:
            return ConnectResult(SessionStatus.NEW, session=dict(session))
        username = str(session.get("bot_username") or "")
        label = f"@{username}" if username else None
        t = self._factory(str(token))
        try:
            await t.call("getMe")
            info: Any = await t.call("getWebhookInfo")
        except TelegramError as exc:
            msg = "Bot token revoked" if exc.code in (401, 404) else str(exc)
            return ConnectResult(SessionStatus.ERROR, message=msg, account_label=label,
                                 session=dict(session))
        url = (info or {}).get("url") or ""
        if url != webhook_url(channel_id):
            return ConnectResult(SessionStatus.ERROR, account_label=label, session=dict(session),
                                 message="Webhook is not set to this server; reconnect the bot")
        return ConnectResult(SessionStatus.ACTIVE, account_label=label, session=dict(session))

    async def disconnect(self, channel_id: int, session: Session) -> None:
        token = session.get("bot_token")
        if not token:
            return
        try:
            await self._factory(str(token)).call("deleteWebhook", {"drop_pending_updates": False})
        except TelegramError as exc:
            if exc.code not in (401, 404):    # revoked token: nothing left to unlink
                raise

    def build_port(self, channel_id: int, session: Session) -> ChannelPort:
        token = session.get("bot_token")
        if not token:
            raise ValueError(f"telegram channel {channel_id} has no bot token")
        return TelegramAdapter(self._factory(str(token)))

    def verify_webhook(self, headers: Mapping[str, str], session: Session) -> bool:
        return secret_matches(header_value(headers, SECRET_HEADER),
                              session.get("webhook_secret"))

    def parse_webhook(self, payload: Mapping[str, Any]) -> list[InboundMessage]:
        return parse_update(payload)
