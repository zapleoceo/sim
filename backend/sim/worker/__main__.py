"""`python -m sim.worker`"""
from __future__ import annotations

import asyncio
import logging

from sim.config import settings
from sim.wiring import build_container
from sim.worker.loop import Worker


async def main() -> None:
    container = build_container(settings())
    try:
        await Worker(container).run()
    finally:
        await container.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
