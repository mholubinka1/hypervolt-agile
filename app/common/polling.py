import asyncio
import logging.config
import time
from collections.abc import Awaitable, Callable
from inspect import iscoroutinefunction
from logging import Logger, getLogger
from typing import Any

from common.constants import APP_NAME
from common.logging import config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

TaskType = Callable[[], None] | Callable[[], Awaitable[None]]
OnTickType = Callable[[], Any] | None


async def every(delay: float, task: TaskType, on_tick: OnTickType = None) -> None:
    _next = time.time()

    while True:
        await asyncio.sleep(max(0, _next - time.time()))
        try:
            if iscoroutinefunction(task):
                await task()  # Run async function in new event loop
            else:
                task()
        except Exception:
            logger.exception("Unhandled exception in scheduled task.")
        if on_tick:
            try:
                on_tick()
            except Exception:
                logger.exception("Unhandled exception in tick callback.")
        _next += (time.time() - _next) // delay * delay + delay
