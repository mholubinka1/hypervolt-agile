from __future__ import annotations

import logging.config
from datetime import timedelta
from logging import Logger, getLogger
from typing import List, Optional, Tuple

from common.constants import APP_NAME, ELECTRICITY_VAT_RATE, SESSION_CLOCK_OFFSET_MINS
from common.logging import config
from common.model import ChargeSession, Price
from common.utils import integer_ceiling_product

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


def build(
    prices: List[Price],
    duration_hrs: float,
    limit_exc_vat: float,
) -> Tuple[List[ChargeSession], Optional[float]]:
    _charging_periods = integer_ceiling_product(duration_hrs, 2)
    _lowest_prices = _select_cheapest(prices, _charging_periods, limit_exc_vat)
    if len(_lowest_prices) < _charging_periods:
        logger.warning(
            f"Insufficient number of periods under the price limit to provide a full charge. Expected total charging time: {len(_lowest_prices) / 2} hours."
        )
    _avg_price = (
        sum(p.value_exc_vat for p in _lowest_prices)
        / len(_lowest_prices)
        * ELECTRICITY_VAT_RATE
        / 100
        if _lowest_prices
        else None
    )
    return _merge_periods(_lowest_prices), _avg_price


def _select_cheapest(
    prices: List[Price],
    number: int,
    limit_exc_vat: float,
) -> List[Price]:
    _filtered = [p for p in prices if p.value_exc_vat < limit_exc_vat]
    _sorted = sorted(_filtered, key=lambda p: p.value_exc_vat)
    return _sorted[:number]


def _merge_periods(price_periods: List[Price]) -> List[ChargeSession]:
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
        ChargeSession(start=s.start + _offset, end=s.end - _offset) for s in _sessions
    ]
