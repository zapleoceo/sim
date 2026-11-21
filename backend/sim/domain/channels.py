"""What every messenger is, in one table (docs/channels.md): its name in the dashboard, what it
can carry, how messages come in and which Procrastinate queue sends through it. A new channel =
a row here + its adapter (login flow, messenger, receiver); nothing else branches on the kind.

Pure values: the reply capabilities (sim/services/capabilities.py), the plan check, the writing
style, the task router and the dashboard all read this table."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from sim.domain.enums import ChannelKind, MessageKind


class ReceiveMode(StrEnum):
    PUSH = "push"           # a live session pushes updates (Telethon)
    POLL = "poll"           # SIM asks the messenger every few minutes (Instagram private API)
    WEBHOOK = "webhook"     # the messenger gateway posts to SIM (Evolution API -> /api/hooks)


@dataclass(frozen=True)
class ChannelSpec:
    kind: ChannelKind
    label: str                          # «Telegram», «Instagram», «WhatsApp»
    url_name: str                       # /personas/{id}/channels/{url_name}/login
    sends: frozenset[MessageKind]       # what the persona can send through it
    typing: bool                        # a live «печатает» / «записывает аудио» status
    read_receipts: bool                 # «прочитано» the client sees
    calls: bool                         # live voice calls (docs/calls.md)
    receive: ReceiveMode
    queue: str                          # the Procrastinate queue of its sends and statuses
    namespace: str                      # task names: f"{namespace}:send_message"

    def can_send(self, kind: str) -> bool:
        return kind in self.sends


TELEGRAM_QUEUE = "telegram"         # the telegram process (holds the Telethon sessions)
CHANNELS_QUEUE = "channels"         # the worker process (HTTP / private API, no live session)

_EVERYTHING = frozenset(MessageKind)

SPECS: Mapping[ChannelKind, ChannelSpec] = {
    ChannelKind.TELEGRAM_USER: ChannelSpec(
        kind=ChannelKind.TELEGRAM_USER, label="Telegram", url_name="telegram",
        sends=_EVERYTHING, typing=True, read_receipts=True, calls=True,
        receive=ReceiveMode.PUSH, queue=TELEGRAM_QUEUE, namespace="telegram"),
    # The private API sends text and photos; voice notes, round videos and the typing status
    # are not in it (docs/channels.md, «Instagram»).
    ChannelKind.INSTAGRAM: ChannelSpec(
        kind=ChannelKind.INSTAGRAM, label="Instagram", url_name="instagram",
        sends=frozenset({MessageKind.TEXT, MessageKind.PHOTO}), typing=False,
        read_receipts=True, calls=False, receive=ReceiveMode.POLL, queue=CHANNELS_QUEUE,
        namespace="channels"),
    # Everything but the round video (WhatsApp has none): a voice note is a real PTT bubble.
    ChannelKind.WHATSAPP: ChannelSpec(
        kind=ChannelKind.WHATSAPP, label="WhatsApp", url_name="whatsapp",
        sends=_EVERYTHING - {MessageKind.VIDEO_NOTE}, typing=True, read_receipts=True,
        calls=False, receive=ReceiveMode.WEBHOOK, queue=CHANNELS_QUEUE, namespace="channels"),
}


def spec_of(kind: ChannelKind | str) -> ChannelSpec:
    """The row of this kind; an unknown kind is a programming error (the DB CHECK has the same
    values)."""
    return SPECS[ChannelKind(kind)]


def kind_by_url_name(name: str) -> ChannelKind | None:
    for spec in SPECS.values():
        if name in (spec.url_name, spec.kind.value):
            return spec.kind
    return None
