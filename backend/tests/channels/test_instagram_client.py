"""The password key of an Instagram login (sim/channels/instagram/client.py): Instagram answers
instagrapi's `qe/sync` 404 now, so the key comes from launcher/sync (prod, 2026-11-28:
«Не удалось войти (TypeError)»). A fake client; nothing reaches Instagram."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sim.channels.instagram.client import (
    KEY_ID_HEADER,
    PUB_KEY_HEADER,
    PasswordKeyMissing,
    launcher_password_key,
)
from sim.channels.instagram.login import error_text

KEYED = {KEY_ID_HEADER: "41", PUB_KEY_HEADER: "LS0tLS1CRUdJTi..."}


class Launcher:
    """`last_response` as instagrapi keeps it; each `sync_launcher` answers the next headers."""

    def __init__(self, last: dict[str, str] | None, *answers: dict[str, str]) -> None:
        self.last_response: Any = SimpleNamespace(headers=last) if last is not None else None
        self.answers = list(answers)
        self.synced: list[bool] = []

    def sync_launcher(self, login: bool = False) -> None:
        self.synced.append(login)
        self.last_response = SimpleNamespace(headers=self.answers.pop(0))


def test_the_key_comes_from_the_launcher_sync_the_login_just_made() -> None:
    client = Launcher(KEYED)
    assert launcher_password_key(client) == (41, "LS0tLS1CRUdJTi...")
    assert client.synced == []                          # no extra request


def test_without_a_key_in_the_last_answer_launcher_sync_is_asked_again() -> None:
    client = Launcher({"ig-set-x-mid": "m"}, KEYED)     # e.g. the 429 pre_login_flow ignored
    assert launcher_password_key(client) == (41, "LS0tLS1CRUdJTi...")
    assert client.synced == [True]
    assert launcher_password_key(Launcher(None, KEYED)) == (41, "LS0tLS1CRUdJTi...")


def test_no_key_at_all_is_a_clear_error_not_a_type_error() -> None:
    client = Launcher({}, {KEY_ID_HEADER: "41"})        # the public key is missing
    with pytest.raises(PasswordKeyMissing) as raised:
        launcher_password_key(client)
    assert client.synced == [True]
    assert "sessionid" in error_text(raised.value)


def test_every_client_takes_the_password_key_from_launcher_sync() -> None:
    pytest.importorskip("instagrapi")
    from sim.channels.instagram.client import build_ig_client

    client = build_ig_client()
    client.last_response = SimpleNamespace(headers=KEYED)
    assert client.password_publickeys() == (41, "LS0tLS1CRUdJTi...")
