#!/usr/bin/python3
"""One-off v1 -> v2 transfer, step 1: read what v2 keeps from the v1 database into ONE JSON file.

Usage: python scripts/export_v1.py <v1-database-url> <out.json> [--persona-id 1]

Reads (docs/deploy.md, "Первый выкат"): the persona (name, prompt from `agent_prompt`, voice,
time zone, birth date, gender, language, model), the `guard_prompt` app setting, the persona's
Telegram userbot channel with `session_enc` AS IS (it stays encrypted with SIM_SECRET_KEY, which
v2 keeps), active admins with a Telegram id, and the persona's media rows (files are copied
separately). Read-only, no network besides the database. The output file is created with mode
0600: it holds the encrypted Telegram session."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

FORMAT = "sim-v1-export"
VERSION = 1

PERSONA_SQL = (
    "SELECT id, name, agent_prompt, voice_id, voice_style, timezone, birth_date, gender, "
    "language, model FROM persona WHERE id = %(persona_id)s"
)
GUARD_SQL = "SELECT value FROM app_setting WHERE key = 'guard_prompt'"
# The live (non-archived, active first, newest) Telegram userbot channel of the persona.
CHANNEL_SQL = (
    "SELECT id, kind, name, account_label, status, session_enc FROM channel "
    "WHERE persona_id = %(persona_id)s AND kind = 'telegram_user' "
    "ORDER BY is_archived ASC, (status = 'active') DESC, id DESC LIMIT 1"
)
ADMINS_SQL = (
    "SELECT telegram_id, display_name, tg_username FROM admin_user "
    "WHERE telegram_id IS NOT NULL AND is_active ORDER BY id"
)
MEDIA_SQL = (
    "SELECT id, kind, storage_name, caption, tags FROM persona_media "
    "WHERE persona_id = %(persona_id)s ORDER BY id"
)


def libpq_url(url: str) -> str:
    """psycopg wants `postgresql://`; v1 settings carry an SQLAlchemy driver (`+asyncpg`)."""
    return re.sub(r"^postgres(?:ql)?(?:\+\w+)?://", "postgresql://", url)


def iso_date(value: date | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


def build_export(
    persona: dict[str, Any], guard_prompt: Any, channel: dict[str, Any] | None,
    admins: list[dict[str, Any]], media: list[dict[str, Any]], *, exported_at: datetime,
) -> dict[str, Any]:
    """The export document. Pure: rows in, JSON-ready dict out."""
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": exported_at.isoformat(),
        "persona": {
            "v1_id": persona["id"],
            "name": persona["name"],
            "prompt": persona.get("agent_prompt") or "",
            "voice_id": persona.get("voice_id") or "",
            "voice_style": persona.get("voice_style") or "",
            "timezone": persona.get("timezone") or "",
            "birth_date": iso_date(persona.get("birth_date")),
            "gender": persona.get("gender") or "",
            "language": persona.get("language") or "",
            "model": persona.get("model") or "",
        },
        "guard_prompt": guard_prompt if isinstance(guard_prompt, str) and guard_prompt else None,
        "channel": None if channel is None else {
            "v1_id": channel["id"],
            "kind": channel["kind"],
            "name": channel.get("name") or "",
            "account_label": channel.get("account_label") or "",
            "status": channel.get("status") or "",
            "session_enc": channel.get("session_enc"),
        },
        "admins": [
            {"telegram_id": int(row["telegram_id"]), "name": row.get("display_name") or "",
             "username": row.get("tg_username") or None}
            for row in admins
        ],
        "media": [
            {"v1_id": row["id"], "kind": row["kind"], "storage_name": row["storage_name"],
             "caption": row.get("caption") or "", "tags": list(row.get("tags") or [])}
            for row in media
        ],
    }


def read_v1(url: str, persona_id: int) -> dict[str, Any]:
    params = {"persona_id": persona_id}
    with psycopg.connect(libpq_url(url), row_factory=dict_row) as conn:
        conn.read_only = True
        persona = conn.execute(PERSONA_SQL, params).fetchone()
        if persona is None:
            raise SystemExit(f"v1 persona {persona_id} not found")
        guard = conn.execute(GUARD_SQL).fetchone()
        channel = conn.execute(CHANNEL_SQL, params).fetchone()
        admins = conn.execute(ADMINS_SQL).fetchall()
        media = conn.execute(MEDIA_SQL, params).fetchall()
    return build_export(persona, guard["value"] if guard else None, channel, admins, media,
                        exported_at=datetime.now(UTC))


def write_private(path: Path, document: dict[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(document, fh, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("database_url", help="v1 database, e.g. postgresql://sim@postgres/sim")
    parser.add_argument("out", type=Path, help="JSON file to write")
    parser.add_argument("--persona-id", type=int, default=1)
    args = parser.parse_args(argv)
    document = read_v1(args.database_url, args.persona_id)
    write_private(args.out, document)
    channel = document["channel"]
    print(f"exported persona {document['persona']['name']!r}, "
          f"channel: {'yes' if channel and channel['session_enc'] else 'none'}, "
          f"admins: {len(document['admins'])}, media: {len(document['media'])} -> {args.out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
