import asyncio
import logging.config
import time
from inspect import iscoroutinefunction
from logging import Logger, getLogger
from typing import Any, Awaitable, Callable, Optional, Union

from common.constants import APP_NAME
from common.logging import config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

TaskType = Union[Callable[[], None], Callable[[], Awaitable[None]]]
OnTickType = Optional[Callable[[], Any]]


async def every(delay: float, task: TaskType, on_tick: OnTickType = None) -> None:
    _next = time.time() + delay

    while True:
        await asyncio.sleep(max(0, _next - time.time()))
        try:
            if iscoroutinefunction(task):
                await task()  # Run async function in new event loop
            else:
                task()
        except Exception as e:
            logger.exception(f"Unhandled exception in scheduled task: {e}")
        if on_tick:
            try:
                on_tick()
            except Exception as e:
                logger.exception(f"Unhandled exception in tick callback: {e}")
        _next += (time.time() - _next) // delay * delay + delay
