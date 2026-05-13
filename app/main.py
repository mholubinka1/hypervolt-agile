import asyncio
import logging.config
import sys
from argparse import ArgumentParser, Namespace
from logging import Logger, getLogger
from pathlib import Path

from common.constants import APP_NAME
from common.logging import config
from schedule import Scheduler, every

from config import ConfigLoader

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

logger.info(f"Starting {APP_NAME}.")

POLLING_FREQUENCY = 10


def parse_args() -> Namespace:
    _parser = ArgumentParser()
    _parser.add_argument("--config-file", type=str, required=True)
    _args = _parser.parse_args()
    return _args


async def main() -> None:
    try:
        args = parse_args()
        config_path = Path(args.config_file)
        config_loader = ConfigLoader(config_path)
        config = config_loader.get_config()
    except Exception as e:
        logger.critical(f"Unable to load startup configuration: {e}")
        sys.exit(1)

    scheduler = Scheduler(
        config=config,
    )
    await every(config.schedule.poll, scheduler.run)


if __name__ == "__main__":
    asyncio.run(main())
