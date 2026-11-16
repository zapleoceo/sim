"""Owner accounts: Telegram users, listed by numeric id in the owner setting
`instant_reply_telegram_ids`, whom the persona answers at once.

In a private Telegram chat (bot or userbot channel) the thread id is the user's id. For a listed
account the reply is planned like a dialog marked «Срочный»: no wait for her routine (work,
road, sleep, other things), and her photos and video notes skip «Пауза между медиа». The reply
debounce (a burst of messages gets one answer) still applies."""
from __future__ import annotations

import re

from sim.adapters.db.models import Dialog
from sim.domain.enums import ChannelKind

TELEGRAM_KINDS = frozenset({ChannelKind.TELEGRAM, ChannelKind.TELEGRAM_USER})
TELEGRAM_ID = re.compile(r"^[1-9]\d{0,15}$")
SEPARATORS = re.compile(r"[\s,;]+")


def parse_ids(text: str) -> tuple[str, ...]:
    """'169510539, 42 42' -> ('169510539', '42'): numeric ids, duplicates dropped, order kept.
    Raises ValueError on anything that is not a positive Telegram user id."""
    ids: list[str] = []
    for part in SEPARATORS.split(text.strip()):
        if not part:
            continue
        if not TELEGRAM_ID.match(part):
            raise ValueError(f"not a numeric Telegram user id: {part!r}")
        if part not in ids:
            ids.append(part)
    return tuple(ids)


def is_owner_account(dialog: Dialog, ids_text: str) -> bool:
    """The private Telegram dialog with one of the listed accounts."""
    return (dialog.channel.kind in TELEGRAM_KINDS
            and dialog.external_thread_id in parse_ids(ids_text))
