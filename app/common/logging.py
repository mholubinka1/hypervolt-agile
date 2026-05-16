import io
import logging.config
import sys
from pathlib import Path

from common.constants import APP_NAME, FILE_LOG_LEVEL, LOG_LEVEL, LOG_MAX_BYTES

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

_FORMATTER = {
    "std_out": {
        "format": "%(asctime)s %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
    },
}

config = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "std_out",
            "stream": "ext://sys.stdout",
            "level": LOG_LEVEL,
        },
    },
    "formatters": _FORMATTER,
    "loggers": {
        APP_NAME: {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        }
    },
}


def configure_file_logging(log_file: str, console_level: str) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "std_out",
                    "stream": "ext://sys.stdout",
                    "level": console_level,
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "std_out",
                    "filename": log_file,
                    "maxBytes": LOG_MAX_BYTES,
                    "backupCount": 7,
                    "level": FILE_LOG_LEVEL,
                    "encoding": "utf-8",
                },
            },
            "formatters": _FORMATTER,
            "loggers": {
                APP_NAME: {
                    "handlers": ["console", "file"],
                    "level": FILE_LOG_LEVEL,
                    "propagate": False,
                }
            },
        }
    )
