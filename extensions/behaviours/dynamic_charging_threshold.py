import asyncio
import math
from logging import Logger, getLogger
from typing import Any

import httpx
from common.constants import APP_NAME
from common.polling import every
from common.utils import is_null_or_empty
from fuel_finder.auth import FuelFinderAuth
from fuel_finder.client import FuelFinderClient

# Deliberately does NOT call logging.config.dictConfig() -- see
# extensions/saints_fc.py's identical comment: this module is loaded
# dynamically at runtime via load_extensions(), after
# configure_file_logging() has already configured the APP_NAME logger with a
# file handler. Re-running dictConfig here would reset it to console-only.
logger: Logger = getLogger(APP_NAME)

_LITRES_PER_GALLON = 4.54609
_SAFETY_MARGIN = 0.8
_DEFAULT_MI_PER_KWH = 3.5
_FUEL_FINDER_BASE_URL = "https://www.fuel-finder.service.gov.uk"
# The extension's fuel_type config maps to the *standard* grade of each --
# petrol -> E10, diesel -> B7_STANDARD -- not the premium variants, matching
# what most UK pumps mean by "petrol"/"diesel" without qualification.
_FUEL_TYPE_CODES = {"petrol": "E10", "diesel": "B7_STANDARD"}


def _dynamic_threshold_incl_vat(
    fuel_price_per_litre: float, mpg: float, mi_per_kwh: float
) -> float:
    _breakeven = (fuel_price_per_litre * _LITRES_PER_GALLON / mpg) * mi_per_kwh
    return _breakeven * _SAFETY_MARGIN


class DynamicChargingThresholdExtension:
    def __init__(self, config: dict[str, Any], update_every_mins: int) -> None:
        _fuel_type = config.get("fuel_type")
        # isinstance guard first -- a non-string, unhashable value (e.g. a
        # YAML list) would otherwise raise TypeError from the `in` check
        # below instead of the intended, actionable ValueError.
        if not isinstance(_fuel_type, str) or _fuel_type not in _FUEL_TYPE_CODES:
            raise ValueError(
                f"fuel_type must be one of {sorted(_FUEL_TYPE_CODES)}, got "
                f"{_fuel_type!r}."
            )
        self._fuel_type_code = _FUEL_TYPE_CODES[_fuel_type]

        self._mpg = self._require_number(config, "mpg")
        self._mi_per_kwh = self._optional_number(
            config, "mi_per_kwh", _DEFAULT_MI_PER_KWH
        )

        _postcode = config.get("postcode")
        if not isinstance(_postcode, str) or is_null_or_empty(_postcode):
            raise ValueError(
                f"postcode is required and must be a non-blank string, got type "
                f"{type(_postcode).__name__}."
            )
        self._postcode = _postcode

        _station_count = config.get("station_count")
        if (
            isinstance(_station_count, bool)
            or not isinstance(_station_count, int)
            or _station_count <= 0
        ):
            raise ValueError(
                f"station_count is required and must be a positive int, got type "
                f"{type(_station_count).__name__}."
            )
        self._station_count = _station_count

        self._radius_miles = self._optional_number_or_none(config, "radius_miles")

        self._client_id = self._require_credential(config, "client_id")
        self._client_secret = self._require_credential(config, "client_secret")

        self._update_every_mins = update_every_mins

        self._client = httpx.AsyncClient(base_url=_FUEL_FINDER_BASE_URL)
        self._auth = FuelFinderAuth(self._client, self._client_id, self._client_secret)
        self._fuel_finder = FuelFinderClient(self._client, self._auth)
        self._threshold: float | None = None
        self._task: asyncio.Task | None = None

    def _require_number(self, config: dict[str, Any], key: str) -> float:
        _value = config.get(key)
        if (
            isinstance(_value, bool)
            or not isinstance(_value, (int, float))
            or not math.isfinite(_value)
            or _value <= 0
        ):
            # A positive, finite number -- isfinite rejects a YAML `.nan` /
            # `.inf`, which would otherwise produce a nonsensical threshold
            # (mpg, mi_per_kwh, radius_miles), matching saints_fc.py's own
            # poll_interval_hours convention. update_every_mins is no longer
            # among these -- it arrives as its own trusted constructor
            # parameter (AppConfig.schedule.frequency, already validated),
            # not read from this config dict at all.
            raise ValueError(
                f"{key} is required and must be a positive, finite number, got "
                f"type {type(_value).__name__}."
            )
        return float(_value)

    def _optional_number(
        self, config: dict[str, Any], key: str, default: float
    ) -> float:
        # A missing key or an explicit `None` both mean "use the default" --
        # covers both an omitted config field and a bare `key:` YAML line
        # with no value.
        if config.get(key) is None:
            return default
        return self._require_number(config, key)

    def _optional_number_or_none(
        self, config: dict[str, Any], key: str
    ) -> float | None:
        if config.get(key) is None:
            return None
        return self._require_number(config, key)

    def _require_credential(self, config: dict[str, Any], key: str) -> str:
        _value = config.get(key)
        if not isinstance(_value, str) or is_null_or_empty(_value):
            # Describe the type, not the value -- client_id/client_secret are
            # credentials, so they must never be echoed into a message a
            # caller might log (mirrors saints_fc.py's api_key validation).
            raise ValueError(
                f"{key} must be a non-blank string, got type "
                f"{type(_value).__name__}."
            )
        return _value

    async def start(self) -> None:
        self._task = asyncio.create_task(
            every(self._update_every_mins * 60, self._poll_once)
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()

    async def get_threshold(self) -> float | None:
        return self._threshold

    def _clear_cache(self, reason: str) -> None:
        logger.warning(
            f"Dynamic charging threshold extension {reason}; clearing the "
            "cached threshold."
        )
        self._threshold = None

    async def _poll_once(self) -> None:
        try:
            _price = await self._fuel_finder.average_price_near(
                self._postcode,
                self._fuel_type_code,
                self._station_count,
                self._radius_miles,
            )
            if _price is None:
                self._clear_cache(
                    f"found no fuel price near {self._postcode!r} for fuel "
                    f"type {self._fuel_type_code!r}"
                )
                return
            if not math.isfinite(_price) or _price <= 0:
                # FuelFinderClient passes the API's raw price straight
                # through with no validation of its own -- a malformed
                # payload (negative, NaN, or infinite) would otherwise cache
                # a threshold that silently breaks every schedule
                # comparison (NaN never compares true; infinity accepts
                # every price), rather than being treated as unavailable
                # data the same way a missing price already is. A bool
                # can't reach here in practice -- average_price_near()'s
                # own sum()/len() division always normalises its return
                # value to a plain float, even if a station's raw price
                # were a JSON `true` (confirmed empirically: Python's true
                # division on any int/bool/float mix always yields float).
                self._clear_cache(
                    f"received an invalid fuel price {_price!r} near "
                    f"{self._postcode!r}"
                )
                return
            _threshold = _dynamic_threshold_incl_vat(
                _price, self._mpg, self._mi_per_kwh
            )
            if not math.isfinite(_threshold) or _threshold <= 0:
                # The raw price alone being finite and positive doesn't
                # guarantee the arithmetic stays finite -- an extreme but
                # valid price (or mpg/mi_per_kwh at the edges of their own
                # valid range) can still overflow to inf or underflow to
                # exactly 0, which would cache a threshold accepting every
                # (or no) electricity price. Same unavailable-data
                # treatment as an invalid raw price.
                self._clear_cache(
                    f"computed an invalid threshold {_threshold!r} from "
                    f"fuel price {_price!r}"
                )
                return
            _previous_display = (
                None if self._threshold is None else f"{self._threshold:.2f}"
            )
            self._threshold = _threshold
            _display = f"{self._threshold:.2f}"
            if _display != _previous_display:
                # Every poll recomputes the threshold, but it only changes
                # when the underlying fuel price actually moves -- logging
                # unconditionally at info level would flood the log with an
                # identical line on every cadence tick (e.g. every 30 min)
                # even while the price is flat for hours. Compared at the
                # same 2dp precision the log line itself displays, rather
                # than exact float equality, so a sub-cent recomputation
                # difference (e.g. from average_price_near()'s summation
                # order varying between polls) doesn't defeat the
                # suppression by looking like a "change" nobody would ever
                # see in the log.
                logger.info(
                    "Dynamic charging threshold extension computed threshold "
                    f"{_display}p/kWh incl VAT from fuel price "
                    f"{_price:.2f}p/litre."
                )
        except Exception as e:
            # Last-resort safety net for anything unexpected (e.g. a
            # config/type error in our own arithmetic) -- FuelFinderClient
            # already catches and logs its own network/HTTP errors and
            # returns None, so this is not duplicating that. A transient
            # failure here should not wipe a still-valid cached threshold
            # from a previous successful poll, so self._threshold is
            # deliberately left untouched.
            logger.warning(
                f"Dynamic charging threshold extension poll failed unexpectedly: "
                f"{type(e).__name__}: {e}."
            )
