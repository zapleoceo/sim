"""instagrapi 3.2 under SIM (sim/channels/instagram/client.py, login.py). On prod (2026-11-28)
2.16 could not log in at all: Instagram answers its `qe/sync` 404 («TypeError») and its legacy
`accounts/login` with `needs_upgrade`. Nothing here reaches Instagram."""
from __future__ import annotations

import pytest

from sim.channels.instagram.login import error_text, two_factor_kind
from tests.fakes.channels import FakeIgClient, TwoFactorRequired


class UnknownError(Exception):
    """instagrapi's ClientError family: `message` is what Instagram said."""

    def __init__(self, message: str, error_type: str = "") -> None:
        super().__init__(message)
        self.message, self.error_type = message, error_type


def test_an_unknown_error_shows_what_instagram_said() -> None:
    exc = UnknownError("Your version of Instagram is out of date. Please upgrade your app to "
                       "log in to Instagram.", "needs_upgrade")
    assert error_text(exc) == ("Не удалось войти (UnknownError: Your version of Instagram is "
                               "out of date. Please upgrade your app to log in to Instagram.)")
    assert error_text(UnknownError("x " * 200)).endswith("x)")
    assert len(error_text(UnknownError("x " * 200))) < 200
    assert error_text(TypeError("int() argument")) == "Не удалось войти (TypeError)"


def test_the_2fa_kind_of_a_legacy_and_of_a_bloks_login() -> None:
    client = FakeIgClient()
    client.last_json = {"two_factor_info": {"totp_two_factor_on": True}}
    assert two_factor_kind(client) == "totp"
    client.last_json = {}
    bloks = TwoFactorRequired("Instagram returned a Bloks two-factor context from the CAA login "
                              "flow; provide verification_code for login")
    assert two_factor_kind(client, bloks) == "code"
    with_context = TwoFactorRequired("2FA")
    with_context.two_step_verification_context = "ctx"  # type: ignore[attr-defined]
    assert two_factor_kind(client, with_context) == "code"
    assert two_factor_kind(client, TwoFactorRequired("approve")) == "device"


def test_the_real_client_logs_in_the_current_way() -> None:
    pytest.importorskip("instagrapi")
    from importlib.metadata import version

    from sim.channels.instagram.client import build_ig_client

    assert tuple(int(part) for part in version("instagrapi").split(".")[:2]) >= (3, 2)
    client = build_ig_client(proxy="socks5://u:p@10.0.0.1:1080", lang="de", tz_offset_s=3600)
    assert client.bloks_versioning_id                   # the CAA login needs it
    assert hasattr(client, "login_legacy") and hasattr(client, "bloks_caa_login")
    assert (client.country, client.locale) == ("DE", "de_DE")
