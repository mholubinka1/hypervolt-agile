from __future__ import annotations

import logging.config
import math
from datetime import timedelta
from logging import Logger, getLogger
from typing import List, Optional, Tuple

from common.constants import APP_NAME, ELECTRICITY_VAT_RATE, SESSION_CLOCK_OFFSET_MINS
from common.logging import config
from common.model import ChargeSession, Price

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


class ScheduleBuilder:
    def __init__(self, duration_hrs: float, limit_exc_vat: float) -> None:
        _periods = duration_hrs * 2  # Agile prices are half-hourly
        self._charging_periods = (
            round(_periods)
            if math.isclose(_periods, round(_periods), abs_tol=1e-9)
            else math.ceil(_periods)
        )
        self._limit_exc_vat = limit_exc_vat

    def build(self, prices: List[Price]) -> Tuple[List[ChargeSession], Optional[float]]:
        _lowest_prices = self._select_cheapest(prices)
        if len(_lowest_prices) < self._charging_periods:
            logger.warning(
                f"Insufficient number of periods under the price limit to provide a full charge. Expected total charging time: {len(_lowest_prices) / 2} hours."
            )
        _average_schedule_price = (
            sum(p.value_exc_vat for p in _lowest_prices)
            / len(_lowest_prices)
            * ELECTRICITY_VAT_RATE
            / 100
            if _lowest_prices
            else None
        )
        return self._merge_periods(_lowest_prices), _average_schedule_price

    def _select_cheapest(self, prices: List[Price]) -> List[Price]:
        _filtered = [p for p in prices if p.value_exc_vat < self._limit_exc_vat]
        _sorted = sorted(_filtered, key=lambda p: p.value_exc_vat)
        return _sorted[: self._charging_periods]

    def _merge_periods(self, price_periods: List[Price]) -> List[ChargeSession]:
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
                _sessions.append(ChargeSession(start=_current_start, end=_current_end))
                _current_start = period.valid_from
                _current_end = period.valid_to
        _sessions.append(ChargeSession(start=_current_start, end=_current_end))

        _offset = timedelta(minutes=SESSION_CLOCK_OFFSET_MINS)
        return [
            ChargeSession(start=s.start + _offset, end=s.end - _offset)
            for s in _sessions
        ]
