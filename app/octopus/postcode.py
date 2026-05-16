import logging.config
from logging import Logger, getLogger

import requests
from common.constants import APP_NAME
from common.decorator import retry
from common.logging import config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


@retry()
def is_valid_postcode(postcode: str) -> bool:
    _response = requests.get(
        url=f"https://api.postcodes.io/postcodes/{postcode}",
        timeout=10,
    )
    if _response.status_code == 404:
        return False
    _response.raise_for_status()
    return True
