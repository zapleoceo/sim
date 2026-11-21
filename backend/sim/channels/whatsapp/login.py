"""Linking a WhatsApp account from the dashboard by QR (docs/channels.md, «Вход в WhatsApp»).
Runs in the `api` process; the state lives in Evolution, so nothing is kept in memory.

  start  -> POST /instance/create (instance `sim-<channel>`, our webhook + a new secret);
            it exists already (403 / 409) -> point its webhook at us with the secret again;
            -> the QR (create answers it, else GET /instance/connect)
            -> the channel stores {instance, webhook_secret} and becomes `pending`
  poll   -> GET /instance/connectionState: `open` -> connected (+phone from fetchInstances),
            else a fresh QR
  cancel -> DELETE /instance/logout + /instance/delete (the phone loses the linked device)

Without `SIM_EVOLUTION_URL` / `SIM_EVOLUTION_API_KEY` (the `whatsapp` compose profile is off)
every step answers «Сервис WhatsApp не запущен на сервере»."""
from __future__ import annotations

import logging
from typing import Any

from sim.channels.login import LoginResult, LoginState
from sim.channels.whatsapp.evolution import (
    EvolutionError,
    EvolutionTransport,
    create_payload,
    instance_name,
    new_secret,
    webhook_config,
)
from sim.channels.whatsapp.parse import connection_state, owner_phone, qr_data_uri

log = logging.getLogger(__name__)

NOT_INSTALLED = "Сервис WhatsApp не запущен на сервере"
QR_HINT = ("Откройте WhatsApp на телефоне: Настройки → Связанные устройства → Привязка "
           "устройства, и отсканируйте QR-код. Код обновляется сам.")
NO_QR = "QR-код ещё готовится, подождите пару секунд"
EXPIRED = "Вход не начат. Нажмите «Подключить» ещё раз."


def hook_url(base: str, channel_id: int) -> str:
    return f"{base.rstrip('/')}/hooks/evolution/{channel_id}"


class WhatsAppLogins:
    def __init__(self, api: EvolutionTransport | None, hooks_base: str) -> None:
        """`api` None = Evolution is not configured on this server."""
        self.api = api
        self.hooks_base = hooks_base

    @property
    def ready(self) -> bool:
        return self.api is not None

    async def start(self, channel_id: int, credentials: dict[str, Any] | None) -> LoginResult:
        if self.api is None:
            return LoginResult(LoginState.ERROR, NOT_INSTALLED)
        instance = str((credentials or {}).get("instance") or instance_name(channel_id))
        secret = str((credentials or {}).get("webhook_secret") or "") or new_secret()
        url = hook_url(self.hooks_base, channel_id)
        stored = {"instance": instance, "webhook_secret": secret}
        try:
            try:
                created = await self.api.request("POST", "/instance/create",
                                                 create_payload(instance, url, secret))
                qr = qr_data_uri(created)
            except EvolutionError as exc:
                if exc.status not in (403, 409):
                    raise
                # The instance is there (a new QR after a cancel, a relink): our webhook again.
                await self.api.request("POST", f"/webhook/set/{instance}",
                                       {"webhook": webhook_config(url, secret)})
                qr = ""
            if not qr:
                return await self._state(channel_id, instance, stored)
        except EvolutionError as exc:
            return self._failed(channel_id, exc)
        log.info("whatsapp login channel=%s: QR shown", channel_id)
        return LoginResult(LoginState.QR, QR_HINT, qr=qr, credentials=stored)

    async def poll(self, channel_id: int, credentials: dict[str, Any] | None) -> LoginResult:
        if self.api is None:
            return LoginResult(LoginState.ERROR, NOT_INSTALLED)
        instance = str((credentials or {}).get("instance") or "")
        if not instance:
            return LoginResult(LoginState.NONE, EXPIRED)
        try:
            return await self._state(channel_id, instance, dict(credentials or {}))
        except EvolutionError as exc:
            return self._failed(channel_id, exc)

    async def _state(self, channel_id: int, instance: str,
                     credentials: dict[str, Any]) -> LoginResult:
        assert self.api is not None
        state = connection_state(await self.api.request(
            "GET", f"/instance/connectionState/{instance}"))
        if state == "open":
            phone = ""
            try:
                phone = owner_phone(await self.api.request(
                    "GET", f"/instance/fetchInstances?instanceName={instance}"))
            except EvolutionError as exc:
                log.info("whatsapp channel=%s: no phone (%s)", channel_id, exc)
            log.info("whatsapp login channel=%s: linked", channel_id)
            return LoginResult(LoginState.CONNECTED, "WhatsApp подключён",
                               credentials=credentials, account_id=phone or None,
                               account_label=phone)
        qr = qr_data_uri(await self.api.request("GET", f"/instance/connect/{instance}"))
        return LoginResult(LoginState.QR, QR_HINT if qr else NO_QR, qr=qr or None,
                           credentials=credentials)

    async def answer(self, channel_id: int, credentials: dict[str, Any] | None,
                     code: str) -> LoginResult:
        """WhatsApp asks for nothing but the scan: the same as `poll`."""
        return await self.poll(channel_id, credentials)

    async def cancel(self, channel_id: int, credentials: dict[str, Any] | None) -> None:
        """Unlink and drop the instance (best effort: a gone instance is fine)."""
        instance = str((credentials or {}).get("instance") or "")
        if self.api is None or not instance:
            return
        for path in (f"/instance/logout/{instance}", f"/instance/delete/{instance}"):
            try:
                await self.api.request("DELETE", path)
            except EvolutionError as exc:
                log.info("whatsapp channel=%s: %s skipped (%s)", channel_id, path.split("/")[2],
                         exc.status)

    @staticmethod
    def _failed(channel_id: int, exc: EvolutionError) -> LoginResult:
        log.warning("whatsapp login channel=%s failed: %s", channel_id, exc)
        if exc.status is None:
            return LoginResult(LoginState.ERROR, f"{NOT_INSTALLED} (нет ответа от Evolution API)")
        return LoginResult(LoginState.ERROR, f"WhatsApp: ошибка Evolution API ({exc.status})")
