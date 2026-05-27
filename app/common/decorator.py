import asyncio
import logging.config
from logging import Logger, getLogger
from typing import Awaitable, Callable, ParamSpec, TypeVar

from common.constants import APP_NAME
from common.logging import config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


P = ParamSpec("P")
R = TypeVar("R")


def retry(
    stop_after: int = 3, retry_delay: int = 10
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            attempt = 1
            while attempt < stop_after:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    logger.warning(
                        f"Attempt {attempt} failed for {func.__name__}: {type(e).__name__}: {e}. Retrying in {retry_delay} seconds."
                    )
                    await asyncio.sleep(retry_delay)
                    attempt += 1
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Error attempting to execute {func}: {type(e).__name__}: {e}. Retries exhausted."
                )
                raise

        return wrapper

    return decorator
