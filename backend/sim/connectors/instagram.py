"""Instagram connector: instagrapi private API, polled by the worker.

Login state across requests: the connector is stateless, so an unfinished login (2FA /
checkpoint) is parked in the session dict under "pending" (the service encrypts it). The live
client is also cached in-process for the same-fingerprint retry; after a restart it is rebuilt
from the stored settings (device uuids ride in them), which Instagram accepts as the same
device. The password is kept ONLY inside "pending" and dropped on success."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any

from sim.adapters.channels.capabilities import Capability
from sim.adapters.channels.ig_client import build_ig_client
from sim.adapters.channels.instagram import IGCursor, InstagramAdapter, InstagrapiTransport
from sim.config import settings
from sim.connectors.spec import Session
from sim.domain.enums import ChannelKind, SessionStatus
from sim.ports.channel import ChannelPort, ConnectResult, InboundMessage

logger = logging.getLogger(__name__)

ClientFactory = Callable[..., Any]

_MANUAL_MARKERS = ("Manual verification required", "Complete verification in the Instagram app")


def _is_manual(exc: Exception) -> bool:
    return any(m in str(exc) for m in _MANUAL_MARKERS)


def _two_factor_kind(cl: Any) -> str:
    """'2fa' = TOTP/SMS code to type; 'device' = approve a push on another device."""
    last = getattr(cl, "last_json", None)
    info = (last.get("two_factor_info") or {}) if isinstance(last, dict) else {}
    return "2fa" if (info.get("totp_two_factor_on") or info.get("sms_two_factor_on")) \
        else "device"


class InstagramConnector:
    kind = ChannelKind.INSTAGRAM
    label = "Instagram"
    polling = True
    poll_interval_setting = "ig_poll_interval_s"
    capabilities = frozenset({Capability.PUBLISH_PHOTO, Capability.DOWNLOAD_MEDIA})

    def __init__(self, client_factory: ClientFactory = build_ig_client) -> None:
        self._factory = client_factory
        self._live: dict[int, Any] = {}          # in-flight login clients
        self._cursors: dict[int, IGCursor] = {}  # polling cursors, survive port rebuilds

    # --- login ---------------------------------------------------------------------------

    async def connect(
        self, channel_id: int, credentials: Mapping[str, str], session: Session
    ) -> ConnectResult:
        username = str(credentials.get("username", "")).strip().lstrip("@")
        password = str(credentials.get("password", ""))
        if not username or not password:
            return ConnectResult(SessionStatus.ERROR, message="username and password required",
                                 session=dict(session))
        proxy = str(credentials.get("proxy") or "").strip() or settings().ig_proxy
        cl = self._factory(None, proxy=proxy, lang=str(credentials.get("lang") or ""))
        pending = {"username": username, "password": password, "proxy": proxy}
        return await self._login(channel_id, cl, pending, kind="login", code="")

    async def verify(self, channel_id: int, code: str, session: Session) -> ConnectResult:
        pending = session.get("pending")
        if not isinstance(pending, dict):
            return ConnectResult(SessionStatus.ERROR, message="No login in progress",
                                 session=dict(session))
        cl = self._live.get(channel_id)
        if cl is None:
            cl = self._factory(pending.get("settings") or None,
                               proxy=str(pending.get("proxy") or ""))
            if pending.get("last_json"):
                cl.last_json = pending["last_json"]
        return await self._login(channel_id, cl, dict(pending),
                                 kind=str(pending.get("kind") or "2fa"), code=code.strip())

    async def _login(
        self, channel_id: int, cl: Any, pending: dict[str, Any], *, kind: str, code: str
    ) -> ConnectResult:
        from instagrapi.exceptions import (  # noqa: PLC0415
            ChallengeRequired,
            TwoFactorRequired,
        )

        user, pw = str(pending["username"]), str(pending.get("password") or "")
        try:
            if kind == "challenge":
                cl.challenge_code_handler = lambda _u, _c: code
                await asyncio.to_thread(cl.challenge_resolve, pending.get("last_json")
                                        or cl.last_json)
            elif code and kind in ("2fa", "device"):
                await asyncio.to_thread(cl.login, user, pw, verification_code=code)
            else:
                await asyncio.to_thread(cl.login, user, pw)
        except TwoFactorRequired as exc:
            if _is_manual(exc):
                return self._park(channel_id, cl, pending, "device", "challenge")
            return self._park(channel_id, cl, pending, _two_factor_kind(cl), "2fa")
        except ChallengeRequired as exc:
            manual = _is_manual(exc)
            return self._park(channel_id, cl, pending, "manual" if manual else "challenge",
                              "challenge", last_json=getattr(cl, "last_json", None))
        except Exception as exc:
            return self._failed(channel_id, pending, kind, exc)
        self._live.pop(channel_id, None)
        return ConnectResult(
            SessionStatus.ACTIVE, account_label=f"@{user}",
            session={"settings": cl.get_settings(), "username": user,
                     "proxy": str(pending.get("proxy") or "")},
        )

    def _park(
        self, channel_id: int, cl: Any, pending: dict[str, Any], kind: str, need: str,
        last_json: Any = None,
    ) -> ConnectResult:
        self._live[channel_id] = cl
        parked: dict[str, Any] = {
            "kind": kind, "username": pending["username"], "proxy": pending.get("proxy", ""),
            "settings": cl.get_settings(),
        }
        if kind != "challenge":                      # a code-only challenge never needs it
            parked["password"] = pending.get("password", "")
        if kind == "challenge" and isinstance(last_json, dict):
            parked["last_json"] = last_json
        msgs = {
            "2fa": "Enter the 2FA code",
            "device": "Approve the login in the Instagram app, then confirm",
            "manual": "Approve the checkpoint in the Instagram app, then confirm",
            "challenge": "Enter the verification code Instagram sent you",
        }
        return ConnectResult(SessionStatus.CHALLENGE, need=need, message=msgs[kind],
                             session={"pending": parked})

    def _failed(
        self, channel_id: int, pending: dict[str, Any], kind: str, exc: Exception
    ) -> ConnectResult:
        name = type(exc).__name__
        if name in ("BadPassword", "BadCredentials", "InvalidUser"):
            msg = "Incorrect username or password"
        else:
            msg = f"{name}: {str(exc)[:200]}"
        logger.warning("IG login failed channel=%d: %s", channel_id, name)   # no secrets
        if kind != "login" and name not in ("BadPassword", "BadCredentials"):
            # A wrong code: keep the flow so the user can retry.
            parked = {k: v for k, v in pending.items() if k != "last_json" or v}
            parked.setdefault("kind", kind)
            return ConnectResult(SessionStatus.CHALLENGE, need="challenge" if kind in
                                 ("challenge", "manual") else "2fa", message=msg,
                                 session={"pending": parked})
        self._live.pop(channel_id, None)
        return ConnectResult(SessionStatus.ERROR, message=msg, session={})

    # --- state / lifecycle ---------------------------------------------------------------

    async def state(self, channel_id: int, session: Session) -> ConnectResult:
        pending = session.get("pending")
        if isinstance(pending, dict):
            need = "challenge" if pending.get("kind") in ("challenge", "manual") else "2fa"
            return ConnectResult(SessionStatus.CHALLENGE, need=need, session=dict(session))
        if not session.get("settings"):
            return ConnectResult(SessionStatus.NEW, session=dict(session))
        user = str(session.get("username") or "")
        status = await self.build_port(channel_id, session).session_status()
        return ConnectResult(status, account_label=f"@{user}" if user else None,
                             session=dict(session))

    async def disconnect(self, channel_id: int, session: Session) -> None:
        # No logout call: it is one more private-API request from a flagged-prone IP, and
        # dropping the stored session is what actually ends our access.
        self._live.pop(channel_id, None)
        self._cursors.pop(channel_id, None)

    def build_port(self, channel_id: int, session: Session) -> ChannelPort:
        dump = session.get("settings")
        if not isinstance(dump, dict) or not dump:
            raise ValueError(f"instagram channel {channel_id} has no active session")
        proxy = str(session.get("proxy") or "") or settings().ig_proxy
        own = str((dump.get("authorization_data") or {}).get("ds_user_id") or "")
        transport = InstagrapiTransport(
            client_factory=lambda: self._factory(dump, proxy=proxy), own_user_id=own)
        cursor = self._cursors.setdefault(channel_id, IGCursor())
        return InstagramAdapter(transport, cursor=cursor)

    def verify_webhook(self, headers: Mapping[str, str], session: Session) -> bool:
        return False

    def parse_webhook(self, payload: Mapping[str, Any]) -> list[InboundMessage]:
        return []
