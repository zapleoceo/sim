"""The one place an instagrapi `Client` is made, and what its errors mean.

Anti-ban rule learned in stepan2 (docs/channels.md): the login AND every later request must reach
Instagram through the SAME proxy with a matching device geo. Logging in through an Indonesian
proxy and then polling from a German server IP looks like a stolen session and gets a
checkpoint. The proxy is runtime state of the client (not in `get_settings()`), so it is applied
on every build; country / locale / time zone follow the proxy only when there is one (a regional
locale over a datacenter IP is a worse mismatch than none). The settings dump (device ids,
cookies) is restored with `set_settings`, never by logging in again.

Login: instagrapi 3.2 logs in the way the current Android app does (Bloks «CAA» login), with
the password key from `launcher/sync`. 2.16 is gone for good (prod, 2026-11-28): Instagram
answers its `qe/sync` 404 («TypeError») and its legacy `accounts/login` with `needs_upgrade`
(«Your version of Instagram is out of date»).

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
