import logging.config
from logging import Logger, getLogger

from common.constants import APP_LOGGER_NAME
from common.logging import config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

print("Hello World!")
