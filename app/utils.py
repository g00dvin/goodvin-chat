import asyncio
import logging
import signal
from typing import Callable, Coroutine, Any


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Telethon и httpx слишком многословны даже в DEBUG — держим на WARNING/INFO
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.INFO if debug else logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def setup_signal_handlers(
    shutdown_coro: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    """Register SIGTERM / SIGINT handlers that trigger graceful shutdown."""
    loop = asyncio.get_event_loop()

    def _handler() -> None:
        loop.create_task(shutdown_coro())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handler)
