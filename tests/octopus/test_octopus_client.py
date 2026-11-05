from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from common.exceptions import APIError
from octopus.client import AgileClient

_GO_TARIFF = "E-1R-GO-VAR-22-10-14-C"
_PREVIOUS_AGILE_TARIFF = "E-1R-AGILE-24-10-01-C"
_UTC = ZoneInfo("UTC")
_HALF_HOUR = timedelta(minutes=30)


def _account(tariff_code: str) -> dict:
    return {
        "properties": [
            {
                "postcode": "SO14 0AA",
                "electricity_meter_points": [
                    {
                        "meters": [{"serial_number": "12345"}],
                        "agreements": [
                            {
                                "tariff_code": _PREVIOUS_AGILE_TARIFF,
                                "valid_from": "2025-05-24T00:00:00+01:00",
                                "valid_to": "2026-01-01T00:00:00Z",
                            },
                            {
                                "tariff_code": tariff_code,
                                "valid_from": "2026-01-01T00:00:00Z",
                                "valid_to": None,
                            },
                        ],
                    }
                ],
            }
        ]
    }


def _rate(value_exc_vat: float, valid_from: datetime, valid_to: datetime) -> dict:
    return {
        "value_exc_vat": value_exc_vat,
        "valid_from": valid_from.isoformat(),
        "valid_to": valid_to.isoformat(),
    }


def _current_half_hour() -> datetime:
    _now = datetime.now(_UTC)
    return _now.replace(minute=30 if _now.minute >= 30 else 0, second=0, microsecond=0)


class _OctopusApi:
    def __init__(self, tariff_code: str, rates: list[dict] | None = None) -> None:
        self._account = _account(tariff_code)
        self._rates = rates or []
        self.rate_paths: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/standard-unit-rates/"):
            self.rate_paths.append(request.url.path)
            return httpx.Response(200, json={"results": self._rates, "next": None})
        return httpx.Response(200, json=self._account)


async def _connect(api: _OctopusApi) -> AgileClient:
    _client = AgileClient("api-key", "A-1234ABCD")
    await _client.close()
    _client._client = httpx.AsyncClient(transport=httpx.MockTransport(api.handle))
    await _client._get_active_tariff()
    return _client


async def test_go_account_starts_and_fetches_go_prices() -> None:
    _api = _OctopusApi(_GO_TARIFF)
    _client = await _connect(_api)

    await _client.get_upcoming_prices()

    assert _api.rate_paths == [
        f"/v1/products/GO-VAR-22-10-14/electricity-tariffs/{_GO_TARIFF}/standard-unit-rates/"
    ]


async def test_go_windows_become_half_hour_prices() -> None:
    _cheap_start = _current_half_hour() + timedelta(hours=1)
    _standard_start = _cheap_start + timedelta(hours=2)
    _api = _OctopusApi(
        _GO_TARIFF,
        rates=[
            _rate(34.5, _standard_start, _standard_start + timedelta(hours=1)),
            _rate(9.4, _cheap_start, _standard_start),
        ],
    )
    _client = await _connect(_api)

    _prices = sorted(await _client.get_upcoming_prices(), key=lambda p: p.valid_from)

    assert [(p.valid_from, p.valid_to, p.value_exc_vat) for p in _prices] == [
        (_cheap_start + _HALF_HOUR * i, _cheap_start + _HALF_HOUR * (i + 1), price)
        for i, price in enumerate([9.4] * 4 + [34.5] * 2)
    ]


async def test_go_window_already_under_way_offers_only_remaining_half_hours() -> None:
    _current_slot = _current_half_hour()
    _api = _OctopusApi(
        _GO_TARIFF,
        rates=[
            _rate(
                9.4,
                _current_slot - timedelta(hours=3),
                _current_slot + timedelta(hours=1),
            )
        ],
    )
    _client = await _connect(_api)

    _prices = await _client.get_upcoming_prices()

    _slot_at_fetch = _current_half_hour()
    _window_end = _current_slot + timedelta(hours=1)
    assert sorted(p.valid_from for p in _prices) == [
        _slot
        for _slot in (_slot_at_fetch, _slot_at_fetch + _HALF_HOUR)
        if _slot < _window_end
    ]


async def test_go_window_not_on_half_hours_ends_with_a_clipped_slot() -> None:
    _start = _current_half_hour() + timedelta(hours=1)
    _end = _start + timedelta(minutes=45)
    _client = await _connect(_OctopusApi(_GO_TARIFF, rates=[_rate(9.4, _start, _end)]))

    _prices = sorted(await _client.get_upcoming_prices(), key=lambda p: p.valid_from)

    assert [(p.valid_from, p.valid_to) for p in _prices] == [
        (_start, _start + _HALF_HOUR),
        (_start + _HALF_HOUR, _end),
    ]


async def test_agile_half_hour_prices_are_unchanged() -> None:
    _first = _current_half_hour() + _HALF_HOUR
    _api = _OctopusApi(
        _PREVIOUS_AGILE_TARIFF,
        rates=[
            _rate(12.0, _first + _HALF_HOUR, _first + _HALF_HOUR * 2),
            _rate(10.0, _first, _first + _HALF_HOUR),
        ],
    )
    _client = await _connect(_api)

    _prices = sorted(await _client.get_upcoming_prices(), key=lambda p: p.valid_from)

    assert [(p.valid_from, p.valid_to, p.value_exc_vat) for p in _prices] == [
        (_first, _first + _HALF_HOUR, 10.0),
        (_first + _HALF_HOUR, _first + _HALF_HOUR * 2, 12.0),
    ]


async def test_unsupported_tariff_fails_at_startup_naming_the_tariff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("common.decorator.asyncio.sleep", AsyncMock())

    with pytest.raises(APIError, match="E-1R-VAR-22-11-01-C"):
        await _connect(_OctopusApi("E-1R-VAR-22-11-01-C"))
