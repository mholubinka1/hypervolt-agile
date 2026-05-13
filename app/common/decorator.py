import logging.config
import time
from logging import Logger, getLogger
from typing import Callable, ParamSpec, TypeVar

from common.constants import APP_NAME
from common.logging import config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


P = ParamSpec("P")
R = TypeVar("R", covariant=True)


def retry(
    stop_after: int = 3, retry_delay: int = 10
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            attempt = 1
            while attempt < stop_after:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    warning = f"Attempt {attempt} failed for {func.__name__}: {e}. \nRetrying in {retry_delay} seconds."
                    logger.warning(warning)

                    time.sleep(retry_delay)
                    attempt += 1
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error = f"Error attempting to execute {func}: {e}. \nRetries exhausted."
                logger.error(error)
                raise e

        return wrapper

    return decorator


def async_retry_with_exponential_backoff(
    stop_after: int = 5,
    initial_delay: int = 1,
    max_delay: int = 60,
    factor: float = 2.0,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            attempt = 1
            delay = initial_delay
            while attempt < stop_after:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    warning = f"Attempt {attempt} failed for {func.__name__}: {e}. \nRetrying in {delay} seconds."
                    logger.warning(warning)

                    time.sleep(delay)
                    delay = int(min(delay * factor, max_delay))
                    attempt += 1
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error = f"Attempt {attempt} failed for {func.__name__}: {e}. \nRetries exhausted."
                logger.error(error)
                raise e

        return wrapper

    return decorator
