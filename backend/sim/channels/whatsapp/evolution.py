"""The Evolution API v2 over HTTP (header `apikey`). One thin `request`, so tests swap the
transport (respx / a fake) and nothing else knows the HTTP shape. A client is opened per call:
an idle process holds no connection to Evolution."""
from __future__ import annotations

import secrets
from typing import Any, Protocol

import httpx

from sim.ports import PortError

TIMEOUT_S = 30.0
EVENTS = ("MESSAGES_UPSERT", "CONNECTION_UPDATE")
SECRET_HEADER = "x-sim-webhook-secret"  # noqa: S105 (a header name, not a secret)


class EvolutionError(PortError):
    """`status` = Evolution's HTTP status (None: not reachable). 5xx and unreachable are worth a
    retry; 4xx is not."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message, retryable=status is None or status >= 500)
        self.status = status


class EvolutionTransport(Protocol):
    async def request(self, method: str, path: str,
                      json: dict[str, Any] | None = None) -> Any: ...


class HttpEvolution:
    def __init__(self, base_url: str, api_key: str, *,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transport = transport

    async def request(self, method: str, path: str,
                      json: dict[str, Any] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=TIMEOUT_S,
                                         headers={"apikey": self.api_key},
                                         transport=self.transport) as client:
                response = await client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise EvolutionError(f"Evolution API unreachable ({type(exc).__name__})") from None
        if response.status_code >= 400:
            # The path names the action and the instance; bodies may echo numbers: not logged.
            raise EvolutionError(f"Evolution {method} {path.split('?')[0]} failed "
                                 f"(HTTP {response.status_code})", status=response.status_code)
        try:
            return response.json()
        except ValueError:
            return {}


def instance_name(channel_id: int) -> str:
    return f"sim-{channel_id}"


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def webhook_config(url: str, secret: str) -> dict[str, Any]:
    """Evolution posts MESSAGES_UPSERT and CONNECTION_UPDATE to SIM with our per-channel secret
    header; `base64` puts incoming media into the event (no second call to fetch it)."""
    return {"enabled": True, "url": url, "byEvents": False, "base64": True,
            "headers": {SECRET_HEADER: secret}, "events": list(EVENTS)}


def create_payload(instance: str, url: str, secret: str) -> dict[str, Any]:
    return {
        "instanceName": instance,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
        "groupsIgnore": True,
        "syncFullHistory": False,
        "alwaysOnline": False,      # do not steal the phone's notifications
        "readMessages": False,      # SIM marks read itself, when she reads
        "readStatus": False,
        "webhook": webhook_config(url, secret),
    }
