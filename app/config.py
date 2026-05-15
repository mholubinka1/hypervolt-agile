from __future__ import annotations

import logging.config
import sys
from logging import Logger, getLogger
from pathlib import Path
from typing import Optional

import yaml
from common.constants import APP_NAME
from common.logging import config
from common.utils import is_null_or_empty
from pydantic import BaseModel, Field, field_validator

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


class Octopus(BaseModel):
    account_number: str
    api_key: str

    @field_validator("account_number", "api_key")
    def must_not_be_empty(cls, v: str) -> str:
        if is_null_or_empty(v):
            raise ValueError(
                "A valid Octopus account number and API key must be provided: https://octopus.energy/dashboard/new/accounts/personal-details/api-access"
            )
        return v


class Hypervolt(BaseModel):
    username: str
    password: str


# TODO: re-add when implementing Volvo support
# class Manufacturer(BaseModel):
#    volvo: Volvo

# class Volvo(BaseModel):
#    key: str
#    username: str
#    password: str


class Schedule(BaseModel):
    duration: float = Field(..., alias="total_charge_duration")
    limit: float = Field(..., alias="price_limit_incl_vat")
    frequency: int = Field(..., alias="update_every_mins")
    poll: int = Field(..., alias="poll_every_secs")


class AppConfig(BaseModel):
    octopus: Octopus
    hypervolt: Hypervolt
    schedule: Schedule
    log_file: Optional[str] = None
    log_level: str = "INFO"

    model_config = {"populate_by_name": True}


class ConfigLoader:
    _config: AppConfig
    _path: Path

    def __init__(self, path: Path) -> None:
        self._path = path
        self._load_config()

    def get_config(self) -> AppConfig:
        return self._config

    def _load_config(self) -> None:
        try:
            _content = yaml.safe_load(self._path.read_text(encoding="utf-8"))
            self._config = AppConfig.model_validate(_content)
            logger.info(f"Successfully loaded settings from {self._path}")
        except Exception as e:
            logger.critical(
                f"Failed to load application settings from {self._path}: {e}"
            )
            sys.exit(1)
