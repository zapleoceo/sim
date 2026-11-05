"""Server operator tools: `python -m sim.cli login-link <telegram_id>`."""
from __future__ import annotations

import argparse
import asyncio
import sys

from sim.adapters.db.session import transaction
from sim.config import settings
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    link = sub.add_parser("login-link", help="print a one-time login URL for an existing admin")
    link.add_argument("telegram_id", type=int)
    args = parser.parse_args(argv)
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
