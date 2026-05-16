import logging.config
from datetime import datetime, timedelta
from logging import Logger, getLogger
from typing import List, Optional
from zoneinfo import ZoneInfo

from common.constants import (
    APP_NAME,
    ELECTRICITY_VAT_RATE,
    SESSION_CLOCK_OFFSET_MINS,
    TIMEZONE,
)
from common.logging import config
from common.model import ChargeSession, Price
from hypervolt.coordinator import HypervoltCoordinator
from hypervolt.model import HypervoltSession, LockStatus, ReleaseState
from octopus.client import AgileClient
from octopus.postcode import is_valid_postcode
from schedule_builder import build

from config import AppConfig

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


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
        if not self._coordinator.is_connected:
            logger.info("Websocket not connected, skipping cycle.")
            return
        try:
            await self._coordinator.refresh()
            await self._update_charging_schedule()
            self._prune_schedule()
            if self._can_push():
                await self._apply_charging_schedule()
            if self._can_push():
                await self._lock_control()
        except Exception as e:
            logger.error(f"Error in scheduler run loop: {e}")
            return

    # region Helpers

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

    # endregion

    # region Schedule

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
        _before = len(self._schedule)
        self._schedule = [s for s in self._schedule if s.end >= _now]
        _pruned = _before - len(self._schedule)
        if _pruned:
            logger.info(
                f"Pruned {_pruned} expired session(s), {len(self._schedule)} remaining."
            )

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
                logger.debug(
                    f"Agile prices updated: {len(self._agile_prices)} periods, valid until {self._time_until}."
                )
                self._schedule, self._average_price_per_kwh = build(
                    self._agile_prices,
                    self._total_charge_duration,
                    self._price_limit_exc_vat,
                )
                logger.info(f"New schedule created: {len(self._schedule)} sessions.")
            except Exception as e:
                logger.error(f"Failed to create charging schedule: {e}")

    # endregion

    # region Control

    def _can_push(self) -> bool:
        if self._coordinator is None:
            return False
        _state = self._coordinator.charger_state
        return (
            _state.car_plugged is True and _state.release_state != ReleaseState.RELEASED
        )

    def _should_unlock(self) -> bool:
        _now = datetime.now(ZoneInfo("UTC"))
        _lookahead = timedelta(minutes=SESSION_CLOCK_OFFSET_MINS)
        return any(s.start - _lookahead <= _now < s.end for s in self._schedule)

    async def _lock_control(self) -> None:
        if self._coordinator is None:
            return
        _desired_locked = not self._should_unlock()
        _current = self._coordinator.charger_state.lock_status
        if _current is not None:
            if _desired_locked and _current in (
                LockStatus.locked,
                LockStatus.pending_lock,
            ):
                return
            if not _desired_locked and _current == LockStatus.unlocked:
                return
        if _desired_locked:
            await self._coordinator.lock()
        else:
            await self._coordinator.unlock()

    async def _apply_charging_schedule(self) -> None:
        if self._coordinator is None:
            raise RuntimeError("Coordinator not initialised before applying schedule.")
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
        _pushed = await self._coordinator.apply_schedule(_hypervolt_sessions)
        if _pushed:
            if _hypervolt_sessions:
                logger.info(
                    f"Scheduled {len(_hypervolt_sessions)} sessions, avg £{self._average_price_per_kwh:.4f}/kWh inc. VAT."
                )
            else:
                logger.info("Scheduled 0 sessions, avg N/A.")

    # endregion
