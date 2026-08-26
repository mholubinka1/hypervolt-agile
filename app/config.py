from __future__ import annotations

import logging.config
import sys
from logging import Logger, getLogger
from pathlib import Path
from typing import Any

import yaml
from common.constants import APP_NAME
from common.logging import config
from common.utils import is_null_or_empty
from hypervolt.led import (
    DEFAULT_BUILT_IN_THEMES,
    REFERENCE_ANCHOR_YEAR,
    parse_window_date,
    window_for_year,
)
from pydantic import BaseModel, Field, field_validator, model_validator

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

_RESERVED_LED_EFFECT_NAMES = {
    theme.effect_name for theme, _, _ in DEFAULT_BUILT_IN_THEMES
}


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
    duration: float = Field(..., alias="total_charge_duration", gt=0, le=24)
    limit: float = Field(..., alias="price_limit_incl_vat", gt=0, le=100)
    frequency: int = Field(..., alias="update_every_mins", gt=0, le=1440)
    poll: int = Field(..., alias="poll_every_secs", ge=2, le=3600)


class _WindowedLedTheme(BaseModel):
    effect: str
    start: str
    end: str

    @field_validator("start", "end")
    def must_be_a_valid_window_date(cls, v: str) -> str:
        _month, _day, _, _ = parse_window_date(v)
        if (_month, _day) == (2, 29):
            raise ValueError(
                f"{v!r}: 29 February is not a valid window boundary -- windows are "
                "materialised against real calendar years and this would crash on "
                "any non-leap year."
            )
        return v

    @model_validator(mode="after")
    def end_must_be_after_start(self) -> _WindowedLedTheme:
        # end_month < start_month is a deliberate year-wrap (e.g. party_mode
        # spans New Year's Eve). But a same-month or later-month pair with the
        # end chronologically before the start (e.g. start="03-16",
        # end="03-14") isn't a wrap -- it produces a window whose end instant
        # never comes after its start, so it can never match any date and
        # would silently never activate.
        _start_window = parse_window_date(self.start)
        _end_window = parse_window_date(self.end)
        _start, _end = window_for_year(
            _start_window, _end_window, anchor_year=REFERENCE_ANCHOR_YEAR
        )
        if _end <= _start:
            raise ValueError(
                f"LED theme {self.effect!r}: window end {self.end!r} is not after "
                f"start {self.start!r} -- this window could never match any date."
            )
        return self


class CustomLedTheme(_WindowedLedTheme):
    @field_validator("effect")
    def must_not_collide_with_a_built_in_theme(cls, v: str) -> str:
        if v in _RESERVED_LED_EFFECT_NAMES:
            raise ValueError(
                f"{v!r} is a built-in theme name and is reserved -- a custom theme "
                "can't reuse it as its own effect name."
            )
        return v


class BuiltInLedTheme(_WindowedLedTheme):
    @field_validator("effect")
    def must_be_a_known_built_in_theme(cls, v: str) -> str:
        if v not in _RESERVED_LED_EFFECT_NAMES:
            raise ValueError(
                f"{v!r} is not a built-in theme -- built_in_themes can only "
                f"configure one of {sorted(_RESERVED_LED_EFFECT_NAMES)}, use "
                "custom_themes for anything else."
            )
        return v


class ExtensionEntry(BaseModel):
    name: str
    config: dict[str, Any] = {}


class LedConfig(BaseModel):
    enabled: bool = True
    brightness: float = Field(0.5, gt=0, le=1)
    built_in_themes: list[BuiltInLedTheme] = []
    custom_themes: list[CustomLedTheme] = []
    extensions: list[ExtensionEntry] = []


class AppConfig(BaseModel):
    octopus: Octopus
    hypervolt: Hypervolt
    schedule: Schedule
    led: LedConfig | None = None
    log_file: str | None = None
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
                f"Failed to load application settings from {self._path}: {type(e).__name__}: {e}"
            )
            sys.exit(1)
