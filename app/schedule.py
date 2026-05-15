import asyncio
import logging.config
import time
from datetime import datetime, timedelta
from inspect import iscoroutinefunction
from logging import Logger, getLogger
from typing import Awaitable, Callable, List, Optional, Union
from zoneinfo import ZoneInfo

from common.constants import (
    APP_NAME,
    ELECTRICITY_VAT_RATE,
    SESSION_CLOCK_OFFSET_MINS,
    TIMEZONE,
)
from common.logging import config
from common.model import ChargeSession, Price
from common.utils import integer_ceiling_product
from hypervolt.coordinator import HypervoltCoordinator
from hypervolt.model import HypervoltSession, ReleaseState
from octopus.client import AgileClient
from octopus.postcode import is_valid_postcode

from config import AppConfig

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

TaskType = Union[Callable[[], None], Callable[[], Awaitable[None]]]


async def every(delay: float, task: TaskType) -> None:
    _next = time.time() + delay

    while True:
        await asyncio.sleep(max(0, _next - time.time()))
        try:
            if iscoroutinefunction(task):
                await task()  # Run async function in new event loop
            else:
                task()
        except Exception as e:
            logger.exception(f"Unhandled exception in scheduled task: {e}")
        _next += (time.time() - _next) // delay * delay + delay


class Scheduler:
    _config: AppConfig

    _agile_client: AgileClient
    _coordinator: Optional[HypervoltCoordinator]
    _timezone: str

    _total_charge_duration: float
    _price_limit_exc_vat: float
    _update_freq: int

    _agile_prices: List[Price]
    _time_until: datetime

    _schedule: List[ChargeSession]
    _average_price_per_kwh: Optional[float]
    _last_schedule_update: Optional[datetime]

    def __init__(
        self,
        config: AppConfig,
    ) -> None:
        self._config = config

        self._agile_client = AgileClient(
            api_key=config.octopus.api_key, account_number=config.octopus.account_number
        )
        if not is_valid_postcode(self._agile_client.postcode):
            raise ValueError(
                f"Invalid GB postcode {self._agile_client.postcode}, can not safely determine timezone."
            )
        self._timezone = TIMEZONE
        self._coordinator = None

        self._total_charge_duration = config.schedule.duration
        self._price_limit_exc_vat = config.schedule.limit / ELECTRICITY_VAT_RATE
        self._update_freq = config.schedule.frequency

        self._agile_prices = []

        self._time_until = datetime.now(ZoneInfo("UTC"))
        self._last_schedule_update = None
        self._schedule: List[ChargeSession] = []
        self._average_price_per_kwh: Optional[float] = None

    async def run(self) -> None:
        if not self._coordinator:
            try:
                self._coordinator = await HypervoltCoordinator.create(
                    config=self._config,
                )
            except Exception as e:
                logger.error(f"Failed to initialise coordinator: {e}")
                return
        try:
            await self._coordinator.refresh()
            _schedule_updated = await self._update_charging_schedule()
            _schedule_pruned = self._prune_schedule()
            if _schedule_updated or _schedule_pruned:
                await self._apply_charging_schedule()
        except Exception as e:
            logger.error(f"Error in scheduler run loop: {e}")
            return

    def _select_lowest_agile_prices(self, number: int) -> List[Price]:
        _filtered = [
            p for p in self._agile_prices if p.value_exc_vat < self._price_limit_exc_vat
        ]
        _sorted = sorted(_filtered, key=lambda p: p.value_exc_vat)
        return _sorted[:number]

    def _split_at_local_midnight(self, session: ChargeSession) -> List[ChargeSession]:
        _tz = ZoneInfo(self._timezone)
        _local_start = session.start.astimezone(_tz)
        _local_end = session.end.astimezone(_tz)
        if _local_start.date() == _local_end.date():
            return [session]
        _local_midnight = datetime.combine(
            _local_start.date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=_tz,
        )
        _utc_midnight = _local_midnight.astimezone(ZoneInfo("UTC"))
        return [
            ChargeSession(start=session.start, end=_utc_midnight),
            ChargeSession(start=_utc_midnight, end=session.end),
        ]

    def _create_sessions_from_price_periods(
        self, price_periods: List[Price]
    ) -> List[ChargeSession]:
        if not price_periods:
            return []
        _sorted = sorted(price_periods, key=lambda p: p.valid_from)
        _sessions = []
        _current_start = _sorted[0].valid_from
        _current_end = _sorted[0].valid_to

        for period in _sorted[1:]:
            if _current_end == period.valid_from:
                _current_end = period.valid_to
            else:
                _sessions.append(
                    ChargeSession(
                        start=_current_start,
                        end=_current_end,
                    )
                )
                _current_start = period.valid_from
                _current_end = period.valid_to
        _sessions.append(
            ChargeSession(
                start=_current_start,
                end=_current_end,
            )
        )

        _offset = timedelta(minutes=SESSION_CLOCK_OFFSET_MINS)
        _sessions = [
            ChargeSession(start=s.start + _offset, end=s.end - _offset)
            for s in _sessions
        ]
        return _sessions

    def _create_new_schedule(self) -> None:
        _charging_periods = integer_ceiling_product(
            self._total_charge_duration, 2
        )  # charging duration is in hours, agile prices are half hourly
        _lowest_prices = self._select_lowest_agile_prices(_charging_periods)
        if len(_lowest_prices) < _charging_periods:
            logger.warning(
                f"Insufficient number of periods under the price limit to provide a full charge. Expected total charging time: {len(_lowest_prices) / 2} hours."
            )
        self._average_price_per_kwh = (
            sum(p.value_exc_vat for p in _lowest_prices)
            / len(_lowest_prices)
            * ELECTRICITY_VAT_RATE
            / 100
            if _lowest_prices
            else None
        )
        self._schedule = self._create_sessions_from_price_periods(_lowest_prices)

    def _should_update(self) -> bool:
        _now = datetime.now(ZoneInfo("UTC"))
        if not self._last_schedule_update:
            self._last_schedule_update = _now
            return True
        if _now - self._last_schedule_update > timedelta(minutes=self._update_freq):
            self._last_schedule_update = _now
            return True
        return False

    async def _update_charging_schedule(self) -> bool:
        if self._should_update():
            try:
                _new_prices = self._agile_client.get_upcoming_prices()
                if not _new_prices:
                    logger.warning(
                        "No Agile prices returned. Skipping schedule update."
                    )
                    return False
                _new_time_until = max(price.valid_to for price in _new_prices)
                if not _new_time_until > self._time_until:
                    logger.info("Agile Prices not updated. No schedule set.")
                    return False

                self._agile_prices = _new_prices
                self._time_until = _new_time_until
                logger.debug(
                    f"Agile prices updated: {len(self._agile_prices)} periods, valid until {self._time_until}."
                )
                self._create_new_schedule()
                logger.info(f"New schedule created: {len(self._schedule)} sessions.")
                return True
            except Exception as e:
                logger.error(f"Failed to create charging schedule: {e}")
        return False

    def _prune_schedule(self) -> bool:
        _now = datetime.now(ZoneInfo("UTC"))
        _before = len(self._schedule)
        self._schedule = [s for s in self._schedule if s.end >= _now]
        _pruned = _before - len(self._schedule)
        if _pruned:
            logger.info(
                f"Pruned {_pruned} expired session(s), {len(self._schedule)} remaining."
            )
        return _pruned > 0

    async def _apply_charging_schedule(self) -> None:
        if self._coordinator is None:
            raise RuntimeError("Coordinator not initialised before applying schedule.")
        _state = self._coordinator.charger_state
        if not _state.car_plugged:
            logger.debug("Car not plugged in, skipping schedule push.")
            return
        if _state.release_state == ReleaseState.RELEASED:
            logger.debug("User override active, skipping schedule push.")
            return
        _now_utc = datetime.now(ZoneInfo("UTC"))
        _hypervolt_sessions = []
        for session in self._schedule:
            for split_session in self._split_at_local_midnight(session):
                if split_session.end > _now_utc:
                    _hypervolt_sessions.append(
                        HypervoltSession.create_from_charge_session(
                            split_session, self._timezone
                        )
                    )
        if _hypervolt_sessions:
            logger.info(
                f"Scheduled {len(_hypervolt_sessions)} sessions, avg £{self._average_price_per_kwh:.4f}/kWh inc. VAT."
            )
        else:
            logger.info("Scheduled 0 sessions, avg N/A.")
        await self._coordinator.apply_schedule(_hypervolt_sessions)
