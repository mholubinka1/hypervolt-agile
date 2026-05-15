import asyncio
import logging.config
import time
from inspect import iscoroutinefunction
from logging import Logger, getLogger
from typing import Awaitable, Callable, Union

from common.constants import APP_NAME
from common.logging import config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

TaskType = Union[Callable[[], None], Callable[[], Awaitable[None]]]


async def every(delay: float, task: TaskType) -> None:
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
        _next += (time.time() - _next) // delay * delay + delay
