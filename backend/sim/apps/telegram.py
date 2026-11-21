"""`python -m sim.apps.telegram`: holds the Telegram sessions — receives, sends (queue
`telegram`) and answers calls. Keeps running with no channels (before the first import)."""
from __future__ import annotations

from sim.apps import run, setup_logging
from sim.config import get_settings
from sim.services.deps import set_deps
from sim.telegram.process import run_telegram_process
from sim.wiring import wire


async def main() -> None:
    setup_logging()
    wired = wire(get_settings())
    set_deps(wired.deps)
    try:
        async with wired.tasks_app.open_async():
            await run_telegram_process(wired.deps, app=wired.tasks_app)
    finally:
        await wired.aclose()


if __name__ == "__main__":
    run(main())
