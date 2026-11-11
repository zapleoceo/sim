"""WhatsApp connector: Evolution API v2 (Baileys) instance per channel, webhook delivery."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sim.adapters.channels.capabilities import Capability
from sim.adapters.channels.wa_parse import parse_evolution_webhook, qr_data_uri
from sim.adapters.channels.whatsapp import (
    EvolutionError,
    EvolutionTransport,
    HttpEvolutionTransport,
    WhatsAppAdapter,
    create_payload,
    instance_name,
    map_state,
    webhook_config,
)
from sim.config import settings
from sim.connectors._common import header_value, new_secret, secret_matches
from sim.connectors.spec import Session
from sim.domain.enums import ChannelKind, SessionStatus
from sim.ports.channel import ChannelPort, ConnectResult, InboundMessage

SECRET_HEADER = "apikey"  # noqa: S105 (header name)


def hook_url(channel_id: int) -> str:
    return f"{settings().evolution_webhook_base.rstrip('/')}/hooks/evolution/{channel_id}"


def default_transport() -> EvolutionTransport:
    cfg = settings()
    return HttpEvolutionTransport(base_url=cfg.evolution_url, api_key=cfg.evolution_api_key)


class WhatsAppConnector:
    kind = ChannelKind.WHATSAPP
    label = "WhatsApp"
    polling = False
    capabilities = frozenset({Capability.DOWNLOAD_MEDIA, Capability.SEND_VOICE})

    def __init__(self, transport_factory: Callable[[], EvolutionTransport] = default_transport):
        self._factory = transport_factory

    async def _qr(self, t: EvolutionTransport, instance: str) -> str:
        try:
            return qr_data_uri(await t.request("GET", f"/instance/connect/{instance}"))
        except EvolutionError:
            return ""

    async def connect(
        self, channel_id: int, credentials: Mapping[str, str], session: Session
    ) -> ConnectResult:
        t = self._factory()
        instance = instance_name(channel_id)
        secret = str(session.get("webhook_secret") or "") or new_secret()
        new_session = {"instance": instance, "webhook_secret": secret}
        qr = ""
        try:
            try:
                created = await t.request(
                    "POST", "/instance/create",
                    create_payload(instance, hook_url(channel_id), secret),
                )
                qr = qr_data_uri(created)
            except EvolutionError as exc:
                if exc.status not in (403, 409):
                    raise
                # Instance exists: make sure OUR secret is what Evolution will send, then
                # ask for a fresh QR.
                await t.request("POST", f"/webhook/set/{instance}",
                                {"webhook": webhook_config(hook_url(channel_id), secret)})
            if not qr:
                qr = await self._qr(t, instance)
        except EvolutionError as exc:
            return ConnectResult(SessionStatus.ERROR, message=str(exc), session=dict(session))
        return ConnectResult(
            SessionStatus.PENDING, qr_data_uri=qr or None, session=new_session,
            message=None if qr else "QR is not ready yet; poll the state again",
        )

    async def verify(self, channel_id: int, code: str, session: Session) -> ConnectResult:
        return await self.state(channel_id, session)

    async def state(self, channel_id: int, session: Session) -> ConnectResult:
        instance = str(session.get("instance") or "")
        if not instance:
            return ConnectResult(SessionStatus.NEW, session=dict(session))
        t = self._factory()
        try:
            raw = await t.request("GET", f"/instance/connectionState/{instance}")
        except EvolutionError as exc:
            if exc.status == 404:
                return ConnectResult(SessionStatus.EXPIRED, message="Instance not found",
                                     session=dict(session))
            return ConnectResult(SessionStatus.ERROR, message=str(exc), session=dict(session))
        status = map_state(str(((raw or {}).get("instance") or {}).get("state") or "close"))
        if status is SessionStatus.ACTIVE:
            return ConnectResult(status, account_label=await self._phone(t, instance),
                                 session=dict(session))
        if status is SessionStatus.PENDING:
            return ConnectResult(status, qr_data_uri=await self._qr(t, instance) or None,
                                 session=dict(session))
        return ConnectResult(status, message="WhatsApp session closed; reconnect",
                             session=dict(session))

    async def _phone(self, t: EvolutionTransport, instance: str) -> str | None:
        try:
            rows: Any = await t.request("GET", f"/instance/fetchInstances?instanceName={instance}")
        except EvolutionError:
            return None
        for row in rows if isinstance(rows, list) else [rows]:
            if not isinstance(row, dict):
                continue
            inst = row.get("instance") if isinstance(row.get("instance"), dict) else row
            owner = str(inst.get("ownerJid") or inst.get("owner") or "")
            digits = owner.partition("@")[0].split(":")[0]
            if digits.isdigit():
                return f"+{digits}"
            number = str(inst.get("number") or "")
            if number.isdigit():
                return f"+{number}"
        return None

    async def disconnect(self, channel_id: int, session: Session) -> None:
        instance = str(session.get("instance") or "")
        if not instance:
            return
        t = self._factory()
        try:
            await t.request("DELETE", f"/instance/logout/{instance}")
        except EvolutionError as exc:
            if exc.status not in (400, 404):    # 400: already logged out
                raise
        try:
            await t.request("DELETE", f"/instance/delete/{instance}")
        except EvolutionError as exc:
            if exc.status != 404:
                raise

    def build_port(self, channel_id: int, session: Session) -> ChannelPort:
        instance = str(session.get("instance") or instance_name(channel_id))
        return WhatsAppAdapter(self._factory(), instance=instance)

    def verify_webhook(self, headers: Mapping[str, str], session: Session) -> bool:
        return secret_matches(header_value(headers, SECRET_HEADER),
                              session.get("webhook_secret"))

    def parse_webhook(self, payload: Mapping[str, Any]) -> list[InboundMessage]:
        return parse_evolution_webhook(payload)
