"""One person in many messengers (docs/leads.md, «Один человек в разных мессенджерах»): what the
merge of two clients keeps, and when it is refused. Pure: no database.

A client has at most one identity per messenger: delivery finds the chat's address by
«client + messenger» (sim/channels/delivery.peer_of). So two clients that both have, say, a
Telegram identity are not merged (and not suggested): they are two Telegram accounts."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime

from sim.domain.channels import spec_of
from sim.domain.enums import ChannelKind

NOTES_SEPARATOR = "\n\n"


def shared_messengers(keep: Iterable[ChannelKind | str],
                      other: Iterable[ChannelKind | str]) -> list[ChannelKind]:
    """The messengers both clients have an identity in (empty = they may be merged)."""
    return sorted({ChannelKind(k) for k in keep} & {ChannelKind(k) for k in other})


def merge_refusal(keep_id: int, other_id: int, keep_kinds: Iterable[ChannelKind | str],
                  other_kinds: Iterable[ChannelKind | str]) -> str | None:
    """Why these two clients can not be merged, in Russian; None = they can."""
    if keep_id == other_id:
        return "Нельзя объединить клиента с самим собой"
    if shared := shared_messengers(keep_kinds, other_kinds):
        names = ", ".join(spec_of(kind).label for kind in shared)
        return (f"У обоих клиентов есть аккаунт в {names}: это два разных аккаунта, "
                "объединить их нельзя")
    return None


@dataclass(frozen=True)
class Profile:
    """The client's own fields a merge combines (the passport has its own rule:
    sim.domain.passport.merge_passports)."""

    name: str = ""
    username: str | None = None
    phone: str | None = None
    timezone: str | None = None
    notes: str = ""
    last_seen_at: datetime | None = None


def merge_profiles(keep: Profile, other: Profile) -> Profile:
    """The kept client's fields win; an empty one takes the other's; the operator's notes are
    joined (a duplicate once); the last contact is the later one."""
    notes = [n.strip() for n in (keep.notes, other.notes) if n.strip()]
    seen = [t for t in (keep.last_seen_at, other.last_seen_at) if t is not None]
    return replace(
        keep,
        name=keep.name or other.name,
        username=keep.username or other.username,
        phone=keep.phone or other.phone,
        timezone=keep.timezone or other.timezone,
        notes=NOTES_SEPARATOR.join(dict.fromkeys(notes)),
        last_seen_at=max(seen) if seen else None,
    )
