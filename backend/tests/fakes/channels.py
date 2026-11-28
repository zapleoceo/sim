"""Fakes of instagrapi's Client and of Evolution API: nothing in the tests reaches Instagram or
WhatsApp. Errors are matched by class NAME in SIM (sim/channels/instagram/client.py), so these
stand-ins carry instagrapi's names."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sim.channels.store import StoredAccount
from sim.domain.enums import ChannelKind, ChannelStatus
from sim.ports import PortError

OWN_ID = "4242"


# --- instagrapi exceptions (by name) -----------------------------------------------------------


class TwoFactorRequired(Exception):
    pass


class ChallengeRequired(Exception):
    pass


class BadPassword(Exception):
    pass


class FeedbackRequired(Exception):
    pass


class LoginRequired(Exception):
    pass


class ClientConnectionError(Exception):
    pass


Step = Callable[["FakeIgClient", str, str, str], Any]


class FakeIgClient:
    """What SIM uses of instagrapi's Client. `logins` are scripted steps, one per `login()`;
    a step may raise, set state or call the challenge handler (as instagrapi does inside)."""

    def __init__(self, settings: dict[str, Any] | None = None, *, proxy: str = "",
                 lang: str = "", tz_offset_s: int | None = None,
                 code_handler: Callable[..., str] | None = None) -> None:
        self.settings = dict(settings or {})
        self.proxy, self.lang, self.tz_offset_s = proxy, lang, tz_offset_s
        self.challenge_code_handler = code_handler
        self.authorization_data: dict[str, Any] = dict(self.settings.get("authorization_data")
                                                       or {})
        self.user_id: Any = None
        self.username: Any = None
        self.last_json: Any = {}
        self.logins: list[Step] = []
        self.login_calls: list[tuple[str, str, str]] = []
        self.threads: dict[str, list[dict[str, Any]]] = {}
        self.sent: list[tuple[str, Any, list[int]]] = []
        self.seen: list[int] = []
        self.send_error: Exception | None = None
        self.poll_error: Exception | None = None
        self.next_id = 100

    # --- login -------------------------------------------------------------------------------

    def signed_in(self, username: str = "olya.ig", user_id: str = OWN_ID) -> bool:
        self.username, self.user_id = username, user_id
        self.authorization_data = {"ds_user_id": user_id, "sessionid": f"{user_id}%3Axyz"}
        self.settings["authorization_data"] = self.authorization_data
        self.settings["cookies"] = {"sessionid": "fresh"}
        return True

    def login(self, username: str, password: str, relogin: bool = False,
              verification_code: str = "") -> bool:
        self.login_calls.append((username, password, verification_code))
        step = self.logins.pop(0) if self.logins else None
        if step is None:
            return self.signed_in(username)
        result = step(self, username, password, verification_code)
        return True if result is None else bool(result)

    def login_by_sessionid(self, sessionid: str) -> bool:
        if not sessionid[:1].isdigit():
            raise LoginRequired("bad sessionid")
        return self.signed_in(user_id=sessionid.split("%3A")[0])

    def get_settings(self) -> dict[str, Any]:
        return dict(self.settings)

    def account_info(self) -> Any:
        if not self.authorization_data:
            raise LoginRequired("no session")
        self.user_id = self.authorization_data.get("ds_user_id")
        return SimpleNamespace(username="olya.json", pk=self.user_id)

    # --- direct ------------------------------------------------------------------------------

    def private_request(self, endpoint: str, data: Any = None, params: Any = None) -> Any:
        if self.poll_error is not None:
            raise self.poll_error
        return {"inbox": {"threads": self.threads.get(endpoint, []), "has_older": False}}

    def _sent(self, what: Any, thread_ids: list[int]) -> Any:
        if self.send_error is not None:
            raise self.send_error
        self.next_id += 1
        self.sent.append((type(what).__name__, what, list(thread_ids)))
        return SimpleNamespace(id=str(self.next_id))

    def direct_send(self, text: str, user_ids: Any = None, thread_ids: Any = None) -> Any:
        return self._sent(text, thread_ids or [])

    def direct_send_photo(self, path: Any, user_ids: Any = None, thread_ids: Any = None) -> Any:
        data = Path(path).read_bytes()          # the file exists while the call runs
        return self._sent(data, thread_ids or [])

    def direct_send_seen(self, thread_id: int) -> Any:
        if self.send_error is not None:
            raise self.send_error
        self.seen.append(thread_id)
        return {"status": "ok"}


class FakeIgFactory:
    """`build_ig_client` stand-in: every client it made, the last one in `last`; `prepare` sets
    up a client right after it is made (scripted logins, threads)."""

    def __init__(self, prepare: Callable[[FakeIgClient], None] | None = None) -> None:
        self.prepare = prepare
        self.made: list[FakeIgClient] = []

    def __call__(self, settings: dict[str, Any] | None = None, **kwargs: Any) -> FakeIgClient:
        client = FakeIgClient(settings, **kwargs)
        if self.prepare is not None:
            self.prepare(client)
        self.made.append(client)
        return client

    @property
    def last(self) -> FakeIgClient:
        return self.made[-1]


def two_factor(kind: str) -> Step:
    """A login that stops at 2FA: totp | sms | device (an approval on another device)."""
    def step(client: FakeIgClient, *_: str) -> None:
        info = {"totp_two_factor_on": kind == "totp", "sms_two_factor_on": kind == "sms"}
        client.last_json = {"two_factor_info": info}
        raise TwoFactorRequired("Two-factor authentication required")
    return step


def bloks_two_factor() -> Step:
    """instagrapi 3.2's CAA (Bloks) login stopping at 2FA: no `two_factor_info`, only this."""
    def step(client: FakeIgClient, *_: str) -> None:
        client.last_json = {"layout": {"bloks_payload": {}}}
        raise TwoFactorRequired("Instagram returned a Bloks two-factor context from the CAA "
                                "login flow; provide verification_code for login")
    return step


def raising(exc: Exception) -> Step:
    def step(*_: Any) -> None:
        raise exc
    return step


def code_is(expected: str) -> Step:
    """A 2FA login with `verification_code`: right -> signed in, wrong -> refused."""
    def step(client: FakeIgClient, username: str, _password: str, code: str) -> bool:
        if code != expected:
            raise TwoFactorRequired("wrong code")
        return client.signed_in(username)
    return step


def challenge(expected: str, where: str = "EMAIL") -> Step:
    """instagrapi resolving a challenge inside login(): it asks the handler for the code."""
    def step(client: FakeIgClient, username: str, *_: str) -> bool:
        assert client.challenge_code_handler is not None
        code = client.challenge_code_handler(username, where)
        if code != expected:
            raise ChallengeRequired("challenge_required")
        return client.signed_in(username)
    return step


# --- the stored account of a channel ----------------------------------------------------------


@dataclass
class FakeStore:
    """`ChannelStore` stand-in keyed by channel id (no database)."""

    accounts: dict[int, dict[str, Any]] = field(default_factory=dict)
    statuses: dict[int, ChannelStatus] = field(default_factory=dict)
    changes: list[tuple[int, ChannelStatus, str | None]] = field(default_factory=list)
    saved: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    kinds: dict[int, ChannelKind] = field(default_factory=dict)

    def add(self, channel_id: int, kind: ChannelKind, credentials: dict[str, Any],
            status: ChannelStatus = ChannelStatus.CONNECTED) -> None:
        self.accounts[channel_id] = credentials
        self.statuses[channel_id] = status
        self.kinds[channel_id] = kind

    async def account(self, channel_id: int, kind: ChannelKind) -> StoredAccount:
        if channel_id not in self.accounts or self.kinds[channel_id] != kind:
            raise PortError(f"{kind} channel {channel_id} not found")
        if self.statuses[channel_id] == ChannelStatus.DISABLED:
            raise PortError("switched off")
        creds = self.accounts[channel_id]
        return StoredAccount(channel_id, 1, kind, self.statuses[channel_id],
                             f"token-{len(self.saved)}", dict(creds))

    async def save(self, channel_id: int, credentials: dict[str, Any]) -> None:
        self.accounts[channel_id] = dict(credentials)
        self.saved.append((channel_id, dict(credentials)))

    async def set_status(self, channel_id: int, status: ChannelStatus, error: str | None, *,
                         at: Any = None) -> None:
        self.statuses[channel_id] = status
        self.changes.append((channel_id, status, error))

    async def connected(self, kind: ChannelKind) -> list[int]:
        return [cid for cid, k in self.kinds.items()
                if k == kind and self.statuses[cid] == ChannelStatus.CONNECTED]


def ig_credentials(own_id: str = OWN_ID) -> dict[str, Any]:
    return {"settings": {"authorization_data": {"ds_user_id": own_id, "sessionid": "x"},
                         "uuids": {"phone_id": "p"}},
            "proxy": "socks5://u:p@10.0.0.1:1080", "username": "olya.ig", "lang": "ru",
            "tz_offset_s": 10800}


# --- Evolution API --------------------------------------------------------------------------


class FakeEvolution:
    """Evolution API stand-in: answers by (method, path prefix), records every request."""

    def __init__(self, answers: dict[tuple[str, str], Any] | None = None) -> None:
        self.answers = dict(answers or {})
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []

    async def request(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        self.requests.append((method, path, json))
        for (verb, prefix), answer in self.answers.items():
            if verb == method and path.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer(json) if callable(answer) else answer
        return {}

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _ in self.requests if method is None or m == method]
