"""Server operator tools.

  python -m sim.cli login-link <telegram_id>   one-time login URL for an existing admin
  python -m sim.cli apply-persona-prompts      OVERWRITE the seeded personas + default strategy
                                               with docs/persona-prompts.md and print the changes
  python -m sim.cli admin-bot-webhook          (re)register the login bot's webhook
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sim.adapters.db.session import transaction
from sim.config import settings
from sim.persona_prompts import apply_persona_prompts
from sim.services.admin_bot import admin_bot_secret, register_webhook
from sim.services.auth import LOGIN_LINK_TTL, AccessDenied
from sim.wiring import build_container


async def login_link(telegram_id: int) -> str:
    """Break-glass: a one-time URL that signs the given active admin in (valid 10 minutes)."""
    cfg = settings()
    container = build_container(cfg)
    try:
        async with transaction(container.factory) as s:
            token = await container.auth(s).create_login_link(telegram_id)
    finally:
        await container.engine.dispose()
    return f"{cfg.public_url.rstrip('/')}/api/auth/link?token={token}"


async def apply_prompts() -> int:
    """Overwrite persona descriptions/prohibitions and the default strategy text (owner-approved)."""
    container = build_container(settings())
    try:
        async with transaction(container.factory) as s:
            changes, missing = await apply_persona_prompts(s)
    finally:
        await container.engine.dispose()
    for change in changes:
        print(change)
    if not changes:
        print("Nothing to change: the texts already match docs/persona-prompts.md.")
    for name in missing:
        print(f"NOT FOUND (skipped): {name}", file=sys.stderr)
    return 1 if missing else 0


async def admin_bot_webhook() -> int:
    cfg = settings()
    container = build_container(cfg)
    try:
        transport = container.admin_transport()
        if transport is None:
            print("SIM_TG_LOGIN_BOT_TOKEN is not set", file=sys.stderr)
            return 1
        await register_webhook(
            transport, cfg.public_url, admin_bot_secret(cfg.secret_key, cfg.tg_login_bot_token)
        )
    finally:
        await container.engine.dispose()
    print(f"Webhook set: {cfg.public_url.rstrip('/')}/hooks/admin-bot")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    link = sub.add_parser("login-link", help="print a one-time login URL for an existing admin")
    link.add_argument("telegram_id", type=int)
    sub.add_parser("apply-persona-prompts", help="overwrite seeded personas and strategy texts")
    sub.add_parser("admin-bot-webhook", help="register the login bot's webhook")
    args = parser.parse_args(argv)
    if args.command == "apply-persona-prompts":
        return asyncio.run(apply_prompts())
    if args.command == "admin-bot-webhook":
        try:
            return asyncio.run(admin_bot_webhook())
        except Exception as exc:  # TelegramError text never includes the token
            print(f"setWebhook failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    try:
        url = asyncio.run(login_link(args.telegram_id))
    except AccessDenied:
        print(f"No active admin with Telegram id {args.telegram_id}", file=sys.stderr)
        return 1
    print(url)
    print(f"One-time link, valid {int(LOGIN_LINK_TTL.total_seconds() // 60)} minutes.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
