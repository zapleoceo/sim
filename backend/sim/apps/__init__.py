"""Process entrypoints: `api` (uvicorn factory), `worker` and `telegram` (`python -m`)."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def run(main: Coroutine[Any, Any, None]) -> None:
    """uvloop on Linux (prod), the default loop elsewhere."""
    try:
        import uvloop
    except ImportError:
        asyncio.run(main)
    else:
        uvloop.run(main)
