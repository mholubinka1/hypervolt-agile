import logging.config
from datetime import datetime, timedelta
from logging import Logger, getLogger
from typing import List, Optional
from zoneinfo import ZoneInfo

from common.constants import APP_NAME, ELECTRICITY_VAT_RATE, TIMEZONE
from common.logging import config
from common.model import ChargeSession, Price
from octopus.client import AgileClient
from schedule.builder import ScheduleBuilder

from config import AppConfig

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


class Scheduler:
    def __init__(self, agile_client: AgileClient, config: AppConfig) -> None:
        self._agile_client = agile_client
        self._timezone = TIMEZONE
        self._update_freq = config.schedule.frequency
        self._builder = ScheduleBuilder(
            duration_hrs=config.schedule.duration,
            limit_exc_vat=config.schedule.limit / ELECTRICITY_VAT_RATE,
        )

        self._agile_prices: List[Price] = []
        self._time_until: datetime = datetime.now(ZoneInfo("UTC"))
        self._last_schedule_update: Optional[datetime] = None
        self._last_schedule_verify: Optional[datetime] = None
        self._schedule: List[ChargeSession] = []
        self._average_price_per_kwh: Optional[float] = None
        self._invalidated: bool = False

    @property
    def schedule(self) -> List[ChargeSession]:
        return self._schedule

    @property
    def average_price_per_kwh(self) -> Optional[float]:
        return self._average_price_per_kwh

    @property
    def timezone(self) -> str:
        return self._timezone

    def invalidate(self) -> None:
        self._invalidated = True

    async def update(self) -> None:
        await self._update_charging_schedule()
        self._prune_schedule()

    def should_verify(self) -> bool:
        _now = datetime.now(ZoneInfo("UTC"))
        if (
            not self._last_schedule_verify
            or _now - self._last_schedule_verify > timedelta(minutes=self._update_freq)
        ):
            self._last_schedule_verify = _now
            return True
        return False

    def _should_update(self) -> bool:
        _now = datetime.now(ZoneInfo("UTC"))
        if not self._last_schedule_update:
            self._last_schedule_update = _now
            return True
        if _now - self._last_schedule_update > timedelta(minutes=self._update_freq):
            self._last_schedule_update = _now
            return True
        return False

    def _prune_schedule(self) -> None:
        _now = datetime.now(ZoneInfo("UTC"))
        _expired = [s for s in self._schedule if s.end < _now]
        self._schedule = [s for s in self._schedule if s.end >= _now]
        for session in _expired:
            logger.info(f"Session expired: {session.format(self._timezone)}.")
        if _expired:
            logger.info(
                f"Pruned {len(_expired)} expired session(s), {len(self._schedule)} remaining."
            )
        elif self._schedule:
            logger.debug(
                f"Schedule intact: {len(self._schedule)} session(s) remaining."
            )

    async def _update_charging_schedule(self) -> None:
        if self._invalidated:
            await self._rebuild_on_replug()
        elif self._should_update():
            await self._rebuild_on_new_prices()

    async def _rebuild_on_replug(self) -> None:
        _now = datetime.now(ZoneInfo("UTC"))
        try:
            _new_prices = await self._agile_client.get_upcoming_prices()
            if not _new_prices:
                logger.warning("No Agile prices returned. Skipping schedule rebuild.")
                return
            self._agile_prices = _new_prices
            self._time_until = max(price.valid_to for price in _new_prices)
            self._last_schedule_update = _now
            _prices_from_now = [p for p in self._agile_prices if p.valid_to > _now]
            self._schedule, self._average_price_per_kwh = self._builder.build(
                _prices_from_now,
            )
            logger.info(
                f"New Schedule created on car plugged in: {len(self._schedule)} sessions."
            )
            for session in self._schedule:
                logger.info(f"Session: {session.format(self._timezone)}.")
            self._invalidated = False
        except Exception as e:
            logger.exception(f"Failed to rebuild schedule on car plugged in: {e}")

    async def _rebuild_on_new_prices(self) -> None:
        try:
            _new_prices = await self._agile_client.get_upcoming_prices()
            if not _new_prices:
                logger.warning("No Agile prices returned. Skipping schedule update.")
                return
            _new_time_until = max(price.valid_to for price in _new_prices)
            if not _new_time_until > self._time_until:
                logger.debug("Agile prices unchanged.")
                return
            self._agile_prices = _new_prices
            self._time_until = _new_time_until
            logger.info(
                f"New Agile prices received: {len(self._agile_prices)} periods, valid until {self._time_until}."
            )
            self._schedule, self._average_price_per_kwh = self._builder.build(
                self._agile_prices,
            )
            logger.info(f"New schedule created: {len(self._schedule)} sessions.")
            for session in self._schedule:
                logger.info(f"Session: {session.format(self._timezone)}.")
        except Exception as e:
            logger.exception(f"Failed to create charging schedule: {e}")
