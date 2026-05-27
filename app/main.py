import asyncio
import logging.config
import sys
import tempfile
import time
from argparse import ArgumentParser, Namespace
from logging import Logger, getLogger
from pathlib import Path

from common.constants import APP_NAME
from common.logging import config, configure_file_logging
from common.polling import every
from octopus.client import AgileClient
from octopus.postcode import is_valid_postcode
from schedule import Scheduler
from schedule.coordinator import ScheduleCoordinator

from config import ConfigLoader

_LIVENESS_FILE = Path(tempfile.gettempdir()) / "healthy"  # nosec B108

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

logger.info(f"Starting {APP_NAME}.")


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
        app_config = config_loader.get_config()
    except Exception as e:
        logger.critical(
            f"Unable to load startup configuration: {type(e).__name__}: {e}"
        )
        sys.exit(1)

    if app_config.log_file:
        configure_file_logging(app_config.log_file, app_config.log_level)

    agile_client = await AgileClient.create(
        api_key=app_config.octopus.api_key,
        account_number=app_config.octopus.account_number,
    )
    if not await is_valid_postcode(agile_client.postcode):
        logger.critical(
            f"Invalid GB postcode {agile_client.postcode}, cannot safely determine timezone."
        )
        await agile_client.close()
        sys.exit(1)

    scheduler = Scheduler(agile_client, app_config)
    coordinator = ScheduleCoordinator(scheduler, app_config)
    _poll = app_config.schedule.poll
    try:
        await every(
            _poll,
            coordinator.run,
            on_tick=lambda: _LIVENESS_FILE.write_text(str(time.time() + _poll * 4)),
        )
    finally:
        await agile_client.close()
        await coordinator.close()


if __name__ == "__main__":
    asyncio.run(main())
