"""Single instagrapi Client factory: proxy, geo, delays and challenge handlers in ONE place.

Anti-ban invariant (learned the hard way in stepan2): login AND the worker must reach IG
through the SAME proxy/IP with a MATCHING geo locale. Log in via an Indonesian proxy then
poll from a German server IP and Instagram sees a geo mismatch and issues a checkpoint.
The proxy is runtime Client state (never in dump_settings), so it must be re-applied on
every build, hence this factory. Geo is aligned ONLY when a proxy is set (a regional locale
over a datacenter IP is a worse mismatch than none)."""
from __future__ import annotations

from contextlib import suppress
from typing import Any

# instagrapi's internal pause between private-API calls (humanlike); sends add their own.
DELAY_RANGE = (2, 5)

_LANG_GEO: dict[str, tuple[str, str]] = {
    "id": ("ID", "id_ID"), "ms": ("MY", "ms_MY"), "en": ("US", "en_US"),
    "ru": ("RU", "ru_RU"), "uk": ("UA", "uk_UA"), "th": ("TH", "th_TH"),
    "vi": ("VN", "vi_VN"), "hi": ("IN", "hi_IN"), "ko": ("KR", "ko_KR"),
    "ja": ("JP", "ja_JP"), "es": ("ES", "es_ES"), "pt": ("PT", "pt_PT"),
    "tr": ("TR", "tr_TR"), "fr": ("FR", "fr_FR"), "de": ("DE", "de_DE"),
    "ar": ("SA", "ar_SA"),
}


def geo_for_lang(lang: str) -> tuple[str, str]:
    return _LANG_GEO.get((lang or "").lower(), ("US", "en_US"))


class CodeRequired(Exception):
    """Raised by the default challenge handler: never block on input() in a server."""


def _no_interactive_code(*_args: Any, **_kwargs: Any) -> str:
    raise CodeRequired("verification code required")


def build_ig_client(
    session_settings: dict[str, Any] | None = None,
    *,
    proxy: str = "",
    lang: str = "",
    tz_offset_h: int | None = None,
) -> Any:
    """Construct an instagrapi Client with proxy, geo and delays applied.

    Proxy and geo are applied AFTER set_settings because restoring settings can reset them."""
    from instagrapi import Client  # noqa: PLC0415 (lazy: keeps instagrapi out of tests)

    cl = Client()
    cl.delay_range = list(DELAY_RANGE)
    cl.request_timeout = 30
    cl.with_challenge_flow = False                 # surface ChallengeRequired to us
    cl.challenge_code_handler = _no_interactive_code
    cl.change_password_handler = _no_interactive_code
    if session_settings:
        cl.set_settings(session_settings)
    if proxy:
        cl.set_proxy(proxy)
        country, locale = geo_for_lang(lang)
        with suppress(Exception):  # older instagrapi builds may lack a setter
            cl.set_country(country)
            cl.set_locale(locale)
            if tz_offset_h is not None:
                cl.set_timezone_offset(int(tz_offset_h) * 3600)
    return cl
