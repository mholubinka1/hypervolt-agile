import logging.config
import time
import traceback
from datetime import datetime, timedelta
from inspect import iscoroutinefunction
from logging import Logger, getLogger
from typing import Awaitable, Callable, List, Optional, Union
from zoneinfo import ZoneInfo

from common.constants import APP_NAME, ELECTRICITY_VAT_RATE
from common.logging import config
from common.model import ChargeSession, Price
from common.utils import integer_ceiling_product
from hypervolt.coordinator import HypervoltCoordinator
from hypervolt.model import HypervoltSession
from hypervolt.state import HypervoltChargerState
from octopus.client import AgileClient

from config import AppConfig

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

TaskType = Union[Callable[[], None], Callable[[], Awaitable[None]]]


async def every(delay: float, task: TaskType) -> None:
    _next = time.time() + delay

    while True:
        time.sleep(max(0, _next - time.time()))
        try:
            if iscoroutinefunction(task):
                await task()  # Run async function in new event loop
            else:
                task()
        except Exception:
            traceback.print_exc()
        _next += (time.time() - _next) // delay * delay + delay


class Scheduler:
    _config: AppConfig

    _agile_client: AgileClient
    _coordinator: Optional[HypervoltCoordinator]
    _charger_state: Optional[HypervoltChargerState]

    _total_charge_duration: float
    _price_limit_exc_vat: float
    _update_freq: int

    _agile_prices: List[Price]
    _time_until: datetime

    _schedule: List[ChargeSession]
    _last_schedule_update: Optional[datetime]

    def __init__(
        self,
        config: AppConfig,
    ) -> None:
        self._config = config
        self._agile_client = AgileClient(
            api_key=config.octopus.api_key, account_number=config.octopus.account_number
        )
        self._total_charge_duration = config.schedule.duration
        self._price_limit_exc_vat = config.schedule.limit / ELECTRICITY_VAT_RATE
        self._update_freq = config.schedule.frequency

        self._agile_prices = []

        self._time_until = datetime.now(ZoneInfo("UTC"))
        self._last_schedule_update = None
        self._schedule: List[ChargeSession] = []

    async def run(self) -> None:
        if not self._coordinator:
            self._coordinator = await HypervoltCoordinator.create(
                config=self._config,
            )
            self._charger_state = await self._coordinator.get_charger_state()
        try:
            await self._update_charging_schedule()
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

    def _split_midnight_session(self, session: ChargeSession) -> List[ChargeSession]:
        _start = session.start
        _end = session.end

        if _start.date() == _end.date():
            return [session]

        _midnight = datetime.combine(
            _start.date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=_start.tzinfo,
        )

        return [
            ChargeSession(start=_start, end=_midnight),
            ChargeSession(start=_midnight, end=_end),
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

        _split_sessions = []
        for session in _sessions:
            _split_sessions.extend(self._split_midnight_session(session))
        return _split_sessions

    def _create_new_schedule(self) -> None:
        _charging_periods = integer_ceiling_product(
            self._total_charge_duration, 2
        )  # charging duration is in hours, agile prices are half hourly
        _lowest_prices = self._select_lowest_agile_prices(_charging_periods)
        if len(_lowest_prices) < _charging_periods:
            logger.warning(
                f"Insufficient number of periods under the price limit to provide a full charge. Expected total charging time: {len(_lowest_prices) / 2} hours"
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

    async def _update_charging_schedule(self) -> None:
        if self._should_update():
            try:
                _new_prices = self._agile_client.get_upcoming_prices()
                if not _new_prices:
                    logger.warning(
                        "No Agile prices returned. Skipping schedule update."
                    )
                    return
                _new_time_until = max(price.valid_to for price in _new_prices)
                if not _new_time_until > self._time_until:
                    logger.info("Agile Prices not updated. No schedule set.")
                    return

                self._agile_prices = _new_prices
                self._time_until = _new_time_until
                self._create_new_schedule()
                return
            except Exception as e:
                logger.error(f"Failed to create charging schedule: {e}")

    async def _apply_charging_schedule(self) -> None:
        if not self._coordinator:
            logger.error("HypervoltCoordinator not initialized, cannot apply state.")
            return
        if not self._schedule:
            return
        _hypervolt_sessions = [
            HypervoltSession.create_from_charge_session(cs) for cs in self._schedule
        ]
        await self._coordinator.apply_schedule(_hypervolt_sessions)
