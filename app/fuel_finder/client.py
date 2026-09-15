import logging.config
import math
from dataclasses import dataclass
from logging import Logger, getLogger

import httpx
from common.constants import APP_NAME
from common.logging import config
from fuel_finder.auth import FuelFinderAuth

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

# A batch returning exactly this many records means more may follow; fewer
# (including zero) means it was the last page.
_BATCH_SIZE = 500
_EARTH_RADIUS_MILES = 3958.8
_GEOCODE_URL = "https://api.postcodes.io/postcodes/{postcode}"


@dataclass
class _Station:
    node_id: str
    latitude: float
    longitude: float


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    _lat1, _lon1, _lat2, _lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    _dlat = _lat2 - _lat1
    _dlon = _lon2 - _lon1
    _a = (
        math.sin(_dlat / 2) ** 2
        + math.cos(_lat1) * math.cos(_lat2) * math.sin(_dlon / 2) ** 2
    )
    return _EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(_a))


class FuelFinderClient:
    def __init__(self, client: httpx.AsyncClient, auth: FuelFinderAuth) -> None:
        self._client = client
        self._auth = auth

    async def _geocode(self, postcode: str) -> tuple[float, float] | None:
        try:
            _response = await self._client.get(
                _GEOCODE_URL.format(postcode=postcode), timeout=10
            )
            if _response.status_code == 404:
                logger.warning(
                    f"postcodes.io does not recognise postcode {postcode!r}."
                )
                return None
            _response.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning(
                f"Geocoding a postcode via postcodes.io failed: {type(e).__name__}."
            )
            return None
        _result = _response.json()["result"]
        return _result["latitude"], _result["longitude"]

    async def _get(self, path: str, params: dict, token: str) -> httpx.Response | None:
        try:
            return await self._client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        except httpx.HTTPError as e:
            logger.warning(f"Request to {path} failed: {type(e).__name__}.")
            return None

    async def _authenticated_get(self, path: str, params: dict) -> list | None:
        _token = await self._auth.get_access_token()
        if _token is None:
            logger.warning(f"No Fuel Finder access token available for {path}.")
            return None

        _response = await self._get(path, params, _token)
        if _response is None:
            return None

        if _response.status_code == 401:
            _response = await self._retry_after_unauthorized(path, params)
            if _response is None:
                return None

        try:
            _response.raise_for_status()
        except httpx.HTTPStatusError:
            logger.warning(
                f"Fuel Finder request to {path} failed with status "
                f"{_response.status_code}."
            )
            return None

        _result: list = _response.json()
        return _result

    async def _retry_after_unauthorized(
        self, path: str, params: dict
    ) -> httpx.Response | None:
        # The cached token was rejected server-side even though our own
        # bookkeeping thought it was still in-date -- force a refresh and
        # retry exactly once. A second 401 means the credentials themselves
        # are the problem, not just a stale cache, so give up rather than
        # looping.
        self._auth.invalidate()
        _token = await self._auth.get_access_token()
        if _token is None:
            logger.warning(f"Re-authenticating with Fuel Finder failed for {path}.")
            return None
        _response = await self._get(path, params, _token)
        if _response is None:
            return None
        if _response.status_code == 401:
            logger.warning(f"Fuel Finder rejected the refreshed token for {path}.")
            return None
        return _response

    async def _fetch_all_stations(self) -> list[_Station] | None:
        _stations: list[_Station] = []
        _batch = 1
        while True:
            _page = await self._authenticated_get(
                "/api/v1/pfs", params={"batch-number": _batch}
            )
            if _page is None:
                return None
            _stations.extend(
                _Station(
                    node_id=s["node_id"],
                    latitude=s["location"]["latitude"],
                    longitude=s["location"]["longitude"],
                )
                for s in _page
            )
            if len(_page) < _BATCH_SIZE:
                return _stations
            _batch += 1

    async def _fetch_all_prices(self) -> dict[str, list[dict]] | None:
        _prices_by_node: dict[str, list[dict]] = {}
        _batch = 1
        while True:
            _page = await self._authenticated_get(
                "/api/v1/pfs/fuel-prices", params={"batch-number": _batch}
            )
            if _page is None:
                return None
            for _entry in _page:
                _prices_by_node[_entry["node_id"]] = _entry["fuel_prices"]
            if len(_page) < _BATCH_SIZE:
                return _prices_by_node
            _batch += 1

    def _price_for(
        self, prices_by_node: dict[str, list[dict]], node_id: str, fuel_type: str
    ) -> float | None:
        _prices = prices_by_node.get(node_id)
        if not _prices:
            return None
        return next((p["price"] for p in _prices if p["fuel_type"] == fuel_type), None)

    async def average_price_near(
        self,
        postcode: str,
        fuel_type: str,
        station_count: int,
        radius_miles: float | None = None,
    ) -> float | None:
        if station_count <= 0:
            raise ValueError(f"station_count must be positive, got {station_count}.")

        _location = await self._geocode(postcode)
        if _location is None:
            return None
        _latitude, _longitude = _location

        _stations = await self._fetch_all_stations()
        if _stations is None:
            return None
        _prices_by_node = await self._fetch_all_prices()
        if _prices_by_node is None:
            return None

        # There's no location filter on the API itself, so nearest-station
        # selection has to be computed client-side: rank every returned
        # station by great-circle distance, then walk outward from the
        # target postcode picking off the ones that actually sell the
        # requested fuel type.
        _by_distance = sorted(
            (
                (_haversine_miles(_latitude, _longitude, s.latitude, s.longitude), s)
                for s in _stations
            ),
            key=lambda pair: pair[0],
        )

        _matching_prices: list[float] = []
        for _distance, _station in _by_distance:
            if radius_miles is not None and _distance > radius_miles:
                # _by_distance is sorted ascending -- every remaining station
                # is at least this far away, so nothing later can qualify.
                break
            _price = self._price_for(_prices_by_node, _station.node_id, fuel_type)
            if _price is None:
                continue
            _matching_prices.append(_price)
            if len(_matching_prices) == station_count:
                break

        if not _matching_prices:
            logger.warning(
                f"No station near {postcode!r} reports fuel type {fuel_type!r}."
            )
            return None
        return sum(_matching_prices) / len(_matching_prices)
