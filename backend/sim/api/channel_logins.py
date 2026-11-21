"""The login flow of every channel kind, behind one shape (docs/api.md, «Канал»): the routes
start a login with the kind's own form, then poll it, answer it (Telegram's 2FA password, an
Instagram code) or cancel it without knowing which messenger it is."""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any, Protocol

from sim.channels.instagram.login import InstagramLogins
from sim.channels.login import LoginResult
from sim.channels.whatsapp.evolution import HttpEvolution
from sim.channels.whatsapp.login import NOT_INSTALLED, WhatsAppLogins
from sim.config import Settings
from sim.domain.channels import SPECS
from sim.domain.enums import ChannelKind
from sim.telegram.login import TelegramLogins

Credentials = dict[str, Any] | None
NO_INSTAGRAPI = "На сервере не установлена библиотека instagrapi"


class LoginFlow(Protocol):
    async def poll(self, channel_id: int, credentials: Credentials) -> LoginResult: ...
    async def answer(self, channel_id: int, credentials: Credentials,
                     value: str) -> LoginResult: ...
    async def cancel(self, channel_id: int, credentials: Credentials) -> None: ...


class TelegramFlow:
    def __init__(self, logins: TelegramLogins) -> None:
        self.logins = logins

    async def poll(self, channel_id: int, credentials: Credentials) -> LoginResult:
        return await self.logins.poll(channel_id)

    async def answer(self, channel_id: int, credentials: Credentials,
                     value: str) -> LoginResult:
        return await self.logins.password(channel_id, value)

    async def cancel(self, channel_id: int, credentials: Credentials) -> None:
        await self.logins.cancel(channel_id)


class InstagramFlow:
    def __init__(self, logins: InstagramLogins) -> None:
        self.logins = logins

    async def poll(self, channel_id: int, credentials: Credentials) -> LoginResult:
        return await self.logins.poll(channel_id)

    async def answer(self, channel_id: int, credentials: Credentials,
                     value: str) -> LoginResult:
        return await self.logins.answer(channel_id, value)

    async def cancel(self, channel_id: int, credentials: Credentials) -> None:
        await self.logins.cancel(channel_id)


@dataclass(frozen=True)
class KindInfo:
    kind: ChannelKind
    label: str
    available: bool
    reason: str = ""

    def view(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "label": self.label, "available": self.available,
                "reason": self.reason}


def instagrapi_installed() -> bool:
    return importlib.util.find_spec("instagrapi") is not None


class ChannelLogins:
    """The pending logins of this process, one flow per kind."""

    def __init__(self, telegram: TelegramLogins, instagram: InstagramLogins,
                 whatsapp: WhatsAppLogins, *, instagram_ready: bool = True) -> None:
        self.telegram = telegram
        self.instagram = instagram
        self.whatsapp = whatsapp
        self.instagram_ready = instagram_ready
        self.flows: dict[ChannelKind, LoginFlow] = {
            ChannelKind.TELEGRAM_USER: TelegramFlow(telegram),
            ChannelKind.INSTAGRAM: InstagramFlow(instagram),
            ChannelKind.WHATSAPP: whatsapp,
        }

    @classmethod
    def from_settings(cls, settings: Settings, *, telegram: TelegramLogins | None = None,
                      instagram: InstagramLogins | None = None,
                      whatsapp: WhatsAppLogins | None = None) -> ChannelLogins:
        """Real flows; tests pass fakes for any of them."""
        if whatsapp is None:
            api = (HttpEvolution(settings.evolution_url, settings.evolution_api_key)
                   if settings.whatsapp_ready else None)
            whatsapp = WhatsAppLogins(api, settings.evolution_hooks_base)
        return cls(telegram or TelegramLogins(),
                   instagram or InstagramLogins(default_proxy=settings.ig_default_proxy),
                   whatsapp,
                   instagram_ready=instagram is not None or instagrapi_installed())

    def flow(self, kind: ChannelKind | str) -> LoginFlow:
        return self.flows[ChannelKind(kind)]

    def kinds(self) -> list[KindInfo]:
        """What the «Подключить» menu offers, with why a kind is not available here."""
        out = []
        for kind, spec in SPECS.items():
            reason = ""
            if kind == ChannelKind.INSTAGRAM and not self.instagram_ready:
                reason = NO_INSTAGRAPI
            elif kind == ChannelKind.WHATSAPP and not self.whatsapp.ready:
                reason = NOT_INSTALLED
            out.append(KindInfo(kind, spec.label, not reason, reason))
        return out

    async def close_all(self, _app: object = None) -> None:
        """Shutdown hook of the API (Litestar passes the app)."""
        await self.telegram.close_all()
        await self.instagram.close_all()
