"""One step of connecting a messenger account from the dashboard, the same shape for every
channel (docs/api.md, «Канал»). Each channel's login flow (Telegram QR, Instagram password /
sessionid / session JSON, WhatsApp QR) answers with a `LoginResult`; the route stores a finished
login encrypted on the channel and returns only `view()` — never credentials."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class LoginState(StrEnum):
    NONE = "none"               # nothing pending for this channel
    QR = "qr"                   # waiting for the scan (Telegram, WhatsApp)
    # A login state name, not a secret.
    PASSWORD = "password"  # noqa: S105
    CODE = "code"               # a 2FA code (Instagram: `detail` = totp | sms)
    DEVICE = "device"           # approve the login in the app on the phone (retried by itself)
    CHALLENGE = "challenge"     # a code Instagram sent by e-mail / SMS (`detail` = where)
    MANUAL = "manual"           # Instagram wants a check in its app first, then «Повторить»
    WAIT = "wait"               # the messenger is still answering (Instagram: up to minutes)
    CONNECTED = "connected"     # signed in: `credentials` are set
    ERROR = "error"             # the login is over; `message` says why


# States in which the dashboard re-reads the login by itself.
POLLED = frozenset({LoginState.QR, LoginState.DEVICE, LoginState.MANUAL, LoginState.WAIT})


@dataclass
class LoginResult:
    state: LoginState
    message: str = ""
    qr: str | None = None                       # data URI of the QR while `state` is qr
    detail: str | None = None                   # which code: totp | sms | email
    # Only for CONNECTED: what the channel stores encrypted — never returned by the API.
    credentials: dict[str, Any] | None = field(default=None, repr=False)
    account_id: str | None = None               # the messenger's own id of the account
    account_label: str = ""                     # @username / +phone
    me: Any = field(default=None, repr=False)   # Telegram: the signed-in user

    def view(self) -> dict[str, Any]:
        """What the dashboard may see (no credentials)."""
        return {"state": self.state.value, "message": self.message, "qr": self.qr,
                "detail": self.detail}
