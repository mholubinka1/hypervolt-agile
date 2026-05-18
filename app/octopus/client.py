from __future__ import annotations

import logging.config
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from logging import Logger, getLogger
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx
from common.constants import APP_NAME
from common.decorator import retry
from common.exceptions import APIError, NullValueError
from common.logging import config
from common.model import Price

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

PRODUCT_CODE_REGEX = "^[A-Z]-[0-9A-Z]+-(?P<product_code>[A-Z0-9-]+)-[A-Z]$"


@dataclass
class Product:
    tariff_code: str
    valid_from: datetime
    valid_to: Optional[datetime]


class AgileClient:
    _api_key: str
    _account_number: str
    _base_url: str = "https://api.octopus.energy/v1/"

    postcode: str
    _active_product: str
    _active_tariff: str

    def __init__(self, api_key: str, account_number: str) -> None:
        self._api_key = api_key
        self._account_number = account_number
        self._client = httpx.AsyncClient(auth=(api_key, ""))
        self.postcode = ""
        self._active_product = ""
        self._active_tariff = ""

    @classmethod
    async def create(cls, api_key: str, account_number: str) -> AgileClient:
        instance = cls(api_key, account_number)
        await instance._get_active_tariff()
        return instance

    async def close(self) -> None:
        await self._client.aclose()

    def _get_price_period(self) -> Tuple[datetime, datetime]:
        _uk_tz = ZoneInfo("Europe/London")
        _utc_tz = ZoneInfo("UTC")

        _day_after_tomorrow = (datetime.now(_uk_tz) + timedelta(days=2)).date()
        _period_to = datetime.combine(_day_after_tomorrow, time(23, 0), tzinfo=_uk_tz)

        return datetime.now(_utc_tz), _period_to.astimezone(_utc_tz)

    def _find_active_tariff(
        self, electricity_meter_points: List[Dict]
    ) -> Tuple[str, str]:
        if len(electricity_meter_points) > 1:
            raise NotImplementedError("Unable to handle multiple MPANs.")

        _meters = electricity_meter_points[0].get("meters", None)
        if not _meters:
            raise ValueError("Meter Serial Number information not available.")
        if len(_meters) == 0:
            raise ValueError("Meter Serial Number information not available.")
        if len(_meters) > 1:
            raise NotImplementedError("Unable to handle multiple SNs per MPAN.")

        _agreements_json = electricity_meter_points[0]["agreements"]
        _products = [
            Product(
                tariff_code=a["tariff_code"].upper(),
                valid_from=datetime.fromisoformat(a["valid_from"]),
                valid_to=(
                    None
                    if not a.get("valid_to", None)
                    else datetime.fromisoformat(a["valid_to"])
                ),
            )
            for a in _agreements_json
        ]

        _now = datetime.now(ZoneInfo("UTC"))
        _products = [
            p
            for p in _products
            if p.valid_from <= _now and (p.valid_to is None or p.valid_to > _now)
        ]
        if len(_products) == 0:
            raise ValueError(
                f"Agreements list contains no currently active agreement: {_agreements_json}"
            )

        _active_product = max(_products, key=lambda p: p.valid_from)
        _active_tariff_code = _active_product.tariff_code
        if "AGILE" not in _active_tariff_code:
            raise ValueError("Only compatible with Agile tariffs.")

        _product_code_match = re.search(PRODUCT_CODE_REGEX, _active_tariff_code)
        if not _product_code_match:
            raise NullValueError(
                f"Active product must have a product code: {_active_tariff_code}"
            )
        _active_product_code = _product_code_match.groupdict()["product_code"]
        return _active_product_code, _active_tariff_code

    @retry()
    async def _get_active_tariff(self) -> None:
        _api_endpoint = self._base_url + f"accounts/{self._account_number}"
        _response = None
        try:
            _response = await self._client.get(url=_api_endpoint, timeout=10)
            _response.raise_for_status()
            _response_json = _response.json()

            _properties = next(iter(_response_json.get("properties", None)), None)
            if _properties is None:
                raise APIError("Failed to retrieve Account properties.")

            self.postcode = re.sub(r"\s", "", _properties.get("postcode", None))

            _electricity_meter_information = _properties.get(
                "electricity_meter_points", None
            )
            self._active_product, self._active_tariff = self._find_active_tariff(
                _electricity_meter_information
            )
        except Exception as e:
            if _response:
                if _response.status_code != 200:
                    _response_json = _response.json()
                    raise APIError(_response_json)
            raise Exception(f"Failed to fetch account/meter information: {e}.") from e

    def _to_upcoming_prices_list(self, results: List[Dict]) -> List[Price]:
        _prices = [
            Price(
                value_exc_vat=r["value_exc_vat"],
                valid_from=datetime.fromisoformat(
                    r["valid_from"].replace("Z", "+00:00")
                ),
                valid_to=datetime.fromisoformat(r["valid_to"].replace("Z", "+00:00")),
            )
            for r in results
        ]
        return _prices

    @retry()
    async def get_upcoming_prices(self) -> List[Price]:
        await self._get_active_tariff()
        _api_endpoint = (
            self._base_url
            + f"products/{self._active_product}/electricity-tariffs/{self._active_tariff}/standard-unit-rates/"
        )
        _response = None
        _period_from, _period_to = self._get_price_period()
        _params = {
            "period_from": _period_from.isoformat().replace("+00:00", "Z"),
            "period_to": _period_to.isoformat().replace("+00:00", "Z"),
        }
        logger.debug(
            f"Fetching Agile prices from {_period_from.isoformat()} to {_period_to.isoformat()}."
        )
        try:
            _response = await self._client.get(
                url=_api_endpoint,
                params=_params,
                timeout=10,
            )
            _response.raise_for_status()
            _response_json = _response.json()

            _prices = self._to_upcoming_prices_list(results=_response_json["results"])
            _next = _response_json["next"]
            if not _next:
                return _prices

            _page_remaining = True
            while _page_remaining:
                if not _next:
                    break
                _next, _response = await self._get_next_price_page(_next)
                _prices.extend(
                    self._to_upcoming_prices_list(_response.json()["results"])
                )
                _page_remaining = True if _next else False

            logger.debug(f"Retrieved {len(_prices)} Agile price periods.")
            return _prices

        except Exception as e:
            if _response:
                if _response.status_code != 200:
                    _response_json = _response.json()
                    raise APIError(_response_json)
            raise Exception(f"Failed to fetch upcoming Agile prices: {e}.") from e

    @retry()
    async def _get_next_price_page(
        self, url: str
    ) -> Tuple[Optional[str], httpx.Response]:
        _response = None
        try:
            _response = await self._client.get(url=url, timeout=10)
            _response.raise_for_status()
            _response_json = _response.json()
            return _response_json["next"], _response
        except Exception as e:
            if _response:
                if _response.status_code != 200:
                    _response_json = _response.json()
                    raise APIError(_response_json)
            raise Exception(
                f"Failed to fetch next page of upcoming Agile prices: {e}."
            ) from e
