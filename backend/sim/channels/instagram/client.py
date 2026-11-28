"""The one place an instagrapi `Client` is made, and what its errors mean.

Anti-ban rule learned in stepan2 (docs/channels.md): the login AND every later request must reach
Instagram through the SAME proxy with a matching device geo. Logging in through an Indonesian
proxy and then polling from a German server IP looks like a stolen session and gets a
checkpoint. The proxy is runtime state of the client (not in `get_settings()`), so it is applied
on every build; country / locale / time zone follow the proxy only when there is one (a regional
locale over a datacenter IP is a worse mismatch than none). The settings dump (device ids,
cookies) is restored with `set_settings`, never by logging in again.

The password key: instagrapi 2.16 asks `GET qe/sync/` for the key that encrypts the password,
but Instagram answers it 404 now (seen on prod 2026-11-28: «Не удалось войти (TypeError)»,
`int(None)` of the missing header). The app gets the same key with `launcher/sync`, the request
the login sends just before, so `launcher_password_key` takes it from that answer.

Errors are told apart by class name, so nothing here imports instagrapi's exceptions."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from enum import StrEnum
from typing import Any, Protocol

# instagrapi's own pause between private-API calls (humanlike); sends add their own.
DELAY_RANGE = (2, 5)
REQUEST_TIMEOUT_S = 30

# persona.language -> (Instagram country, locale), aligned only with a proxy.
LANG_GEO: dict[str, tuple[str, str]] = {
    "ru": ("RU", "ru_RU"), "uk": ("UA", "uk_UA"), "en": ("US", "en_US"), "id": ("ID", "id_ID"),
    "ms": ("MY", "ms_MY"), "th": ("TH", "th_TH"), "vi": ("VN", "vi_VN"), "hi": ("IN", "hi_IN"),
    "ko": ("KR", "ko_KR"), "ja": ("JP", "ja_JP"), "es": ("ES", "es_ES"), "pt": ("PT", "pt_PT"),
    "tr": ("TR", "tr_TR"), "fr": ("FR", "fr_FR"), "de": ("DE", "de_DE"), "ar": ("SA", "ar_SA"),
    "pl": ("PL", "pl_PL"), "kk": ("KZ", "kk_KZ"),
}
MANUAL_MARKERS = ("manual verification required", "complete verification in the instagram app")
SOFT_BLOCK_MARKERS = ("feedback_required", "please wait", "too many requests", "rate limit")


KEY_ID_HEADER = "ig-set-password-encryption-key-id"
PUB_KEY_HEADER = "ig-set-password-encryption-pub-key"


class CodeRequired(Exception):
    """Instagram asked for a code nobody typed in time (a server never waits on input())."""


class PasswordKeyMissing(Exception):
    """Instagram gave no key to encrypt the password with (neither answer of launcher/sync)."""


def launcher_password_key(client: Any) -> tuple[int, str]:
    """(key id, public key) for the password, from launcher/sync: the answer the login's
    `pre_login_flow` just got, or a fresh one when that answer has no key (a 429 it ignored)."""
    for attempt in range(2):
        if attempt:
            client.sync_launcher(True)
        headers = getattr(getattr(client, "last_response", None), "headers", None) or {}
        key_id, key = headers.get(KEY_ID_HEADER), headers.get(PUB_KEY_HEADER)
        if key_id and key:
            return int(key_id), str(key)
    raise PasswordKeyMissing("no password encryption key in launcher/sync")


class IgClient(Protocol):
    """What SIM uses of instagrapi's `Client` (tests pass a fake)."""

    last_json: Any
    user_id: Any
    username: Any
    authorization_data: Any

    def login(self, username: str, password: str, relogin: bool = False,
              verification_code: str = "") -> bool: ...
    def login_by_sessionid(self, sessionid: str) -> bool: ...
    def get_settings(self) -> dict[str, Any]: ...
    def account_info(self) -> Any: ...
    def private_request(self, endpoint: str, data: Any = None, params: Any = None) -> Any: ...
    def direct_send(self, text: str, user_ids: Any = None, thread_ids: Any = None) -> Any: ...
    def direct_send_photo(self, path: Any, user_ids: Any = None,
                          thread_ids: Any = None) -> Any: ...
    def direct_send_seen(self, thread_id: int) -> Any: ...


ClientFactory = Callable[..., Any]


def geo_for_lang(lang: str) -> tuple[str, str]:
    return LANG_GEO.get((lang or "").lower()[:2], ("US", "en_US"))


def build_ig_client(session_settings: dict[str, Any] | None = None, *, proxy: str = "",
                    lang: str = "", tz_offset_s: int | None = None,
                    code_handler: Callable[..., str] | None = None) -> Any:
    """An instagrapi Client with delays, the stored device / session, the proxy and its geo.
    `code_handler(username, choice)` answers a challenge; by default nobody does."""
    from instagrapi import Client  # lazy: keeps instagrapi out of tests and idle processes

    client = Client()
    client.delay_range = list(DELAY_RANGE)
    client.request_timeout = REQUEST_TIMEOUT_S
    client.challenge_code_handler = code_handler or no_code
    client.change_password_handler = no_code
    client.password_publickeys = lambda: launcher_password_key(client)   # qe/sync is gone
    if session_settings:
        client.set_settings(session_settings)
    if proxy:
        client.set_proxy(proxy)
        country, locale = geo_for_lang(lang)
        with suppress(Exception):           # older builds may lack a setter
            client.set_country(country)
            client.set_locale(locale)
            if tz_offset_s is not None:
                client.set_timezone_offset(int(tz_offset_s))
    return client


def no_code(*_args: Any, **_kwargs: Any) -> str:
    raise CodeRequired("verification code required")


def own_user_id(client: Any) -> str:
    """Our numeric Instagram id, "" when unknown (then nothing may be told apart: fail closed)."""
    auth = getattr(client, "authorization_data", None) or {}
    found = (auth.get("ds_user_id") if isinstance(auth, dict) else None) \
        or getattr(client, "user_id", None)
    return str(found or "")


class IgError(StrEnum):
    CHALLENGE = "challenge"         # checkpoint / logged out: the owner must log in again
    SOFT_BLOCK = "soft_block"       # «please wait», feedback_required, 429: wait 15 min
    NETWORK = "network"             # timeout, proxy, connection: try again soon
    PERMANENT = "permanent"         # no such thread / user, bad request: do not repeat
    OTHER = "other"


CHALLENGE_NAMES = frozenset({
    "ChallengeRequired", "ChallengeError", "ChallengeUnknownStep", "LoginRequired",
    "ReloginAttemptExceeded", "SelectContactPointRecoveryForm", "RecaptchaChallengeForm",
    "SubmitPhoneNumberForm", "CheckpointRequired", "ConsentRequired", "GeoBlockRequired",
    "AccountSuspended", "TermsAccept"})
SOFT_BLOCK_NAMES = frozenset({
    "FeedbackRequired", "PleaseWaitFewMinutes", "RateLimitError", "ClientThrottledError",
    "SentryBlock", "DirectThreadsPendingInboxError"})
NETWORK_NAMES = frozenset({
    "ClientConnectionError", "ClientRequestTimeout", "ProxyAddressIsBlocked", "ConnectionError",
    "ConnectTimeout", "ReadTimeout", "ProxyError", "TimeoutError", "ClientIncompleteReadError"})
PERMANENT_NAMES = frozenset({
    "DirectThreadNotFound", "DirectMessageNotFound", "UserNotFound", "ClientNotFoundError",
    "ClientBadRequestError", "ValidationError", "PrivateAccount", "ClientForbiddenError"})


def classify(exc: BaseException) -> IgError:
    name = type(exc).__name__
    text = str(exc).lower()
    if name in CHALLENGE_NAMES or "challenge_required" in text or "checkpoint" in text:
        return IgError.CHALLENGE
    if name in SOFT_BLOCK_NAMES or any(marker in text for marker in SOFT_BLOCK_MARKERS):
        return IgError.SOFT_BLOCK
    if name in NETWORK_NAMES or isinstance(exc, OSError | TimeoutError):
        return IgError.NETWORK
    if name in PERMANENT_NAMES:
        return IgError.PERMANENT
    return IgError.OTHER


def is_manual(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in MANUAL_MARKERS)
