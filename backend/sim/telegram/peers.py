"""Telegram users <-> `Peer`. One place that knows which senders are people and how to address
a person after a restart (StringSession keeps no entity cache)."""
from __future__ import annotations

from typing import Any

from sim.ports import Peer

SERVICE_USER_ID = 777000            # Telegram's own notifications account
DIALOG_WARMUP_LIMIT = 100           # dialogs listed once to learn access hashes


def is_person(user: Any) -> bool:
    """A real person: not a bot, not deleted, not ourselves, not Telegram's service account."""
    user_id = int(getattr(user, "id", 0) or 0)
    return bool(
        user is not None and user_id and user_id != SERVICE_USER_ID
        and not getattr(user, "bot", False) and not getattr(user, "deleted", False)
        and not getattr(user, "is_self", False)
    )


def peer_from_user(user: Any) -> Peer:
    name = " ".join(
        str(part) for part in (getattr(user, "first_name", None), getattr(user, "last_name", None))
        if part
    ).strip()
    phone = str(getattr(user, "phone", "") or "").lstrip("+")
    access_hash = getattr(user, "access_hash", None)
    return Peer(
        external_id=str(int(user.id)),
        access_hash=str(access_hash) if access_hash is not None else None,
        name=name,
        username=getattr(user, "username", None) or None,
        phone=f"+{phone}" if phone else None,
    )


async def input_peer(client: Any, peer: Peer) -> Any:
    """InputPeerUser from the stored access hash; without it, Telethon's cache, warmed by one
    dialog listing when the user is unknown."""
    user_id = int(peer.external_id)
    if peer.access_hash:
        from telethon import types

        return types.InputPeerUser(user_id=user_id, access_hash=int(peer.access_hash))
    try:
        return await client.get_input_entity(user_id)
    except ValueError:
        await client.get_dialogs(limit=DIALOG_WARMUP_LIMIT)
        return await client.get_input_entity(user_id)
