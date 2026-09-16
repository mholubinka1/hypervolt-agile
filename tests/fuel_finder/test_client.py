import logging
from collections.abc import Callable
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fuel_finder.auth import FuelFinderAuth
from fuel_finder.client import FuelFinderClient, FuelPriceFailure

_GEOCODE_RESULT = {
    "status": 200,
    "result": {"postcode": "SW1A 1AA", "latitude": 51.5, "longitude": -0.14},
}


def _auth(token: str = "the-token") -> Mock:
    _mock = Mock(spec=FuelFinderAuth)
    _mock.get_access_token = AsyncMock(return_value=token)
    _mock.invalidate = Mock()
    return _mock


def _mock_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://www.fuel-finder.service.gov.uk",
    )


def _station(node_id: str, latitude: float, longitude: float) -> dict:
    return {
        "node_id": node_id,
        "trading_name": "TEST STATION",
        "location": {
            "address_line_1": "1 TEST STREET",
            "postcode": "TE5 7ST",
            "latitude": latitude,
            "longitude": longitude,
        },
        "fuel_types": ["E10"],
    }


def _price_entry(node_id: str, fuel_type: str, price: float) -> dict:
    return {
        "node_id": node_id,
        "trading_name": "TEST STATION",
        "fuel_prices": [
            {
                "fuel_type": fuel_type,
                "price": price,
                "price_last_updated": "2026-09-07T20:37:43.000Z",
                "price_change_effective_timestamp": "2026-09-07T20:37:43.000Z",
            }
        ],
    }


def _router(
    *,
    pfs_batches: list[list[dict]],
    price_batches: list[list[dict]],
    geocode_status: int = 200,
) -> Callable[[httpx.Request], httpx.Response]:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            if geocode_status == 404:
                return httpx.Response(404, json={"status": 404, "error": "not found"})
            return httpx.Response(200, json=_GEOCODE_RESULT)

        if request.url.path == "/api/v1/pfs":
            _pages = pfs_batches
        elif request.url.path == "/api/v1/pfs/fuel-prices":
            _pages = price_batches
        else:
            raise AssertionError(f"Unexpected request: {request.url}")

        _batch = int(request.url.params["batch-number"])
        _page = _pages[_batch - 1] if _batch <= len(_pages) else []
        return httpx.Response(200, json=_page)

    return _handler


async def test_average_price_near_pages_through_multiple_station_batches() -> None:
    # Scenario 5: the station list spans two /pfs batches (first exactly 500,
    # second fewer) -- a station that only appears in the second batch must
    # still be collected and correctly contribute to the average.
    _filler_batch = [
        _station(f"filler-{i}", latitude=60.0, longitude=1.0) for i in range(500)
    ]
    _second_batch_station = _station(
        "only-in-second-batch", latitude=51.5, longitude=-0.14
    )
    _prices = [_price_entry("only-in-second-batch", "E10", 150.0)]

    client = _mock_client(
        _router(
            pfs_batches=[_filler_batch, [_second_batch_station]],
            price_batches=[_prices],
        )
    )
    fuel_finder = FuelFinderClient(client, _auth())

    _average = await fuel_finder.average_price_near("SW1A 1AA", "E10", station_count=1)

    assert _average == 150.0


async def test_average_price_near_pages_through_multiple_price_batches() -> None:
    # _fetch_all_prices() has its own separate pagination loop from
    # _fetch_all_stations() -- a regression specific to it wouldn't be
    # caught by the station-pagination test above alone (PR #163 review).
    _station_entry = _station(
        "only-in-second-price-batch", latitude=51.5, longitude=-0.14
    )
    _filler_prices = [_price_entry(f"filler-{i}", "E10", 999.0) for i in range(500)]
    _matching_price = [_price_entry("only-in-second-price-batch", "E10", 150.0)]

    client = _mock_client(
        _router(
            pfs_batches=[[_station_entry]],
            price_batches=[_filler_prices, _matching_price],
        )
    )
    fuel_finder = FuelFinderClient(client, _auth())

    _average = await fuel_finder.average_price_near("SW1A 1AA", "E10", station_count=1)

    assert _average == 150.0


async def test_average_price_near_rejects_a_non_positive_station_count() -> None:
    # Without this guard, station_count=0's "stop once we have N matches"
    # condition can never become true, so the method silently scans and
    # averages the entire national dataset instead of rejecting the
    # obviously-invalid request (PR #163 review).
    client = _mock_client(_router(pfs_batches=[], price_batches=[]))
    fuel_finder = FuelFinderClient(client, _auth())

    with pytest.raises(ValueError, match="station_count"):
        await fuel_finder.average_price_near("SW1A 1AA", "E10", station_count=0)


async def test_average_price_near_uses_nearest_stations_that_report_the_fuel_type() -> (
    None
):
    # Scenario 6: some nearer stations don't sell the requested fuel type --
    # the mean must be over the nearest stations that actually report it, not
    # simply the physically nearest N.
    _stations = [
        _station("very-near-no-e10", latitude=51.5005, longitude=-0.14),
        _station("near-e10", latitude=51.51, longitude=-0.14),
        _station("mid-no-e10", latitude=51.6, longitude=-0.14),
        _station("far-e10", latitude=52.0, longitude=-0.14),
        _station("very-far-e10", latitude=55.0, longitude=-0.14),
    ]
    _prices = [
        _price_entry("very-near-no-e10", "B7_STANDARD", 999.0),
        _price_entry("near-e10", "E10", 130.0),
        _price_entry("mid-no-e10", "B7_STANDARD", 998.0),
        _price_entry("far-e10", "E10", 150.0),
        _price_entry("very-far-e10", "E10", 200.0),
    ]
    client = _mock_client(_router(pfs_batches=[_stations], price_batches=[_prices]))
    fuel_finder = FuelFinderClient(client, _auth())

    _average = await fuel_finder.average_price_near("SW1A 1AA", "E10", station_count=2)

    assert _average == 140.0


async def test_average_price_near_excludes_stations_outside_radius_miles() -> None:
    # A station selling the requested fuel type but well outside radius_miles
    # must not be averaged in, even if it would otherwise be picked to reach
    # station_count.
    _within_radius = _station("within-radius", latitude=51.5, longitude=-0.14)
    _outside_radius = _station("outside-radius", latitude=52.0, longitude=-0.14)
    _prices = [
        _price_entry("within-radius", "E10", 130.0),
        _price_entry("outside-radius", "E10", 999.0),
    ]
    client = _mock_client(
        _router(
            pfs_batches=[[_within_radius, _outside_radius]], price_batches=[_prices]
        )
    )
    fuel_finder = FuelFinderClient(client, _auth())

    _average = await fuel_finder.average_price_near(
        "SW1A 1AA", "E10", station_count=2, radius_miles=10.0
    )

    assert _average == 130.0


async def test_average_price_near_returns_geocode_failed_when_the_postcode_does_not_geocode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _mock_client(_router(pfs_batches=[], price_batches=[], geocode_status=404))
    fuel_finder = FuelFinderClient(client, _auth())

    with caplog.at_level(logging.WARNING):
        _result = await fuel_finder.average_price_near(
            "NOT A REAL POSTCODE", "E10", station_count=1
        )

    assert _result is FuelPriceFailure.GEOCODE_FAILED
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_average_price_near_returns_geocode_failed_on_a_postcodes_io_5xx(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The 404 test above covers "postcode not recognised"; postcodes.io can
    # also fail with a 5xx -- a real HTTP response reaching _geocode's own
    # raise_for_status() call, distinct from the 404 branch (which returns
    # before ever calling raise_for_status()) and from a transport-level
    # failure (no response at all, covered by the network-exception test
    # below) -- but which must map to the same GEOCODE_FAILED result.
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _mock_client(_handler)
    fuel_finder = FuelFinderClient(client, _auth())

    with caplog.at_level(logging.WARNING):
        _result = await fuel_finder.average_price_near(
            "SW1A 1AA", "E10", station_count=1
        )

    assert _result is FuelPriceFailure.GEOCODE_FAILED
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_average_price_near_returns_geocode_failed_on_a_network_level_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A transport-level exception (no HTTP response at all) is a distinct
    # code path from the 5xx test above -- it never reaches
    # raise_for_status(), failing instead at the request itself -- but must
    # map to the same GEOCODE_FAILED result.
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _mock_client(_handler)
    fuel_finder = FuelFinderClient(client, _auth())

    with caplog.at_level(logging.WARNING):
        _result = await fuel_finder.average_price_near(
            "SW1A 1AA", "E10", station_count=1
        )

    assert _result is FuelPriceFailure.GEOCODE_FAILED
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_average_price_near_returns_no_matching_station_when_no_station_reports_the_fuel_type() -> (
    None
):
    # Scenario 7: the mocked dataset sells other fuel types but never the
    # requested one -- FuelPriceFailure.NO_MATCHING_STATION, not an
    # exception or an empty-list error.
    _stations = [
        _station("s1", latitude=51.5, longitude=-0.14),
        _station("s2", latitude=51.6, longitude=-0.14),
    ]
    _prices = [
        _price_entry("s1", "B7_STANDARD", 190.0),
        _price_entry("s2", "B7_STANDARD", 191.0),
    ]
    client = _mock_client(_router(pfs_batches=[_stations], price_batches=[_prices]))
    fuel_finder = FuelFinderClient(client, _auth())

    _result = await fuel_finder.average_price_near("SW1A 1AA", "E10", station_count=2)

    assert _result is FuelPriceFailure.NO_MATCHING_STATION


async def test_average_price_near_retries_once_after_a_401_and_succeeds() -> None:
    # Scenario 8: a data batch 401s once -- the client refreshes the token via
    # FuelFinderAuth (invalidate() then get_access_token() again) and retries
    # that same request, succeeding on retry.
    _station_list = [_station("s1", latitude=51.5, longitude=-0.14)]
    _price_list = [_price_entry("s1", "E10", 140.0)]
    _pfs_calls = {"n": 0}
    _pfs_auth_headers: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json=_GEOCODE_RESULT)
        if request.url.path == "/api/v1/pfs":
            _pfs_calls["n"] += 1
            _pfs_auth_headers.append(request.headers["authorization"])
            if _pfs_calls["n"] == 1:
                return httpx.Response(
                    401, json={"success": False, "message": "expired"}
                )
            return httpx.Response(200, json=_station_list)
        if request.url.path == "/api/v1/pfs/fuel-prices":
            return httpx.Response(200, json=_price_list)
        raise AssertionError(f"Unexpected request: {request.url}")

    client = _mock_client(_handler)
    _fuel_finder_auth = _auth()
    _token_calls = {"n": 0}

    async def _next_token() -> str:
        # The stations batch 401s on its first (stale) token and succeeds on
        # its second (fresh) one; the subsequent prices batch reuses that
        # fresh token without needing another refresh.
        _token_calls["n"] += 1
        return "stale-token" if _token_calls["n"] == 1 else "fresh-token"

    _fuel_finder_auth.get_access_token = AsyncMock(side_effect=_next_token)
    fuel_finder = FuelFinderClient(client, _fuel_finder_auth)

    _average = await fuel_finder.average_price_near("SW1A 1AA", "E10", station_count=1)

    assert _average == 140.0
    _fuel_finder_auth.invalidate.assert_called_once()
    assert _fuel_finder_auth.get_access_token.await_count == 3
    # Proves the Authorization header actually carries each token, and the
    # retry genuinely sends the refreshed one, not a repeat of the stale one
    # -- a regression dropping or freezing the header would still pass the
    # assertions above (they only check FuelFinderAuth's own call counts).
    assert _pfs_auth_headers == ["Bearer stale-token", "Bearer fresh-token"]


async def test_average_price_near_returns_stations_unavailable_when_401_persists_after_refresh() -> (
    None
):
    # Scenario 9: the refresh doesn't fix it -- a second 401 on the retried
    # request means give up and return FuelPriceFailure.STATIONS_UNAVAILABLE
    # rather than looping forever.
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json=_GEOCODE_RESULT)
        return httpx.Response(401, json={"success": False, "message": "expired"})

    client = _mock_client(_handler)
    _fuel_finder_auth = _auth()
    fuel_finder = FuelFinderClient(client, _fuel_finder_auth)

    _result = await fuel_finder.average_price_near("SW1A 1AA", "E10", station_count=1)

    assert _result is FuelPriceFailure.STATIONS_UNAVAILABLE
    # Proves the retry genuinely happened exactly once, not zero times (the
    # 401 silently swallowed) and not more than once (looping) -- a plain
    # assert on the final result alone can't distinguish those cases.
    _fuel_finder_auth.invalidate.assert_called_once()


async def test_average_price_near_returns_stations_unavailable_and_logs_a_warning_on_a_5xx(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 10: a 5xx from a data batch is caught, logged as a warning, and
    # never propagates out of the client.
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json=_GEOCODE_RESULT)
        return httpx.Response(500)

    client = _mock_client(_handler)
    fuel_finder = FuelFinderClient(client, _auth())

    with caplog.at_level(logging.WARNING):
        _result = await fuel_finder.average_price_near(
            "SW1A 1AA", "E10", station_count=1
        )

    assert _result is FuelPriceFailure.STATIONS_UNAVAILABLE
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_average_price_near_returns_prices_unavailable_when_only_prices_fail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Every other failing-request test above fails on the FIRST authenticated
    # call (/api/v1/pfs, the station list), so they all assert
    # STATIONS_UNAVAILABLE -- none of them distinguishes it from
    # PRICES_UNAVAILABLE. Here the station list fetch succeeds and only the
    # price-list fetch (/api/v1/pfs/fuel-prices) 5xxs, proving
    # average_price_near() reports the failure specific to whichever fetch
    # actually failed, not just "the first thing that could fail".
    _stations = [_station("s1", latitude=51.5, longitude=-0.14)]

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json=_GEOCODE_RESULT)
        if request.url.path == "/api/v1/pfs":
            return httpx.Response(200, json=_stations)
        if request.url.path == "/api/v1/pfs/fuel-prices":
            return httpx.Response(500)
        raise AssertionError(f"Unexpected request: {request.url}")

    client = _mock_client(_handler)
    fuel_finder = FuelFinderClient(client, _auth())

    with caplog.at_level(logging.WARNING):
        _result = await fuel_finder.average_price_near(
            "SW1A 1AA", "E10", station_count=1
        )

    assert _result is FuelPriceFailure.PRICES_UNAVAILABLE
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_average_price_near_returns_stations_unavailable_on_a_network_level_exception() -> (
    None
):
    # Scenario 10: a transport-level exception (no HTTP response at all) is
    # caught the same way as an HTTP-level 5xx.
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json=_GEOCODE_RESULT)
        raise httpx.ConnectError("connection refused", request=request)

    client = _mock_client(_handler)
    fuel_finder = FuelFinderClient(client, _auth())

    _result = await fuel_finder.average_price_near("SW1A 1AA", "E10", station_count=1)

    assert _result is FuelPriceFailure.STATIONS_UNAVAILABLE


async def test_a_failure_does_not_leak_credentials_or_tokens_into_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 11: mirrors
    # tests/extensions/test_saints_fc.py::test_a_poll_failure_does_not_leak_the_api_key_into_logs.
    # Fuel Finder's secrets travel in a JSON body (client_id/client_secret) and
    # a Bearer header (access_token/refresh_token) rather than in the URL, so
    # the leak surface is different -- a real auth flow runs first so a real
    # access_token/refresh_token are in play, then the data request fails.
    _secrets = {
        "client_id": "MY-CLIENT-ID",
        "client_secret": "MY-CLIENT-SECRET",
        "access_token": "MY-ACCESS-TOKEN",
        "refresh_token": "MY-REFRESH-TOKEN",
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json=_GEOCODE_RESULT)
        if request.url.path == "/api/v1/oauth/generate_access_token":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "access_token": _secrets["access_token"],
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "refresh_token": _secrets["refresh_token"],
                        "refresh_token_expires_in": 172800,
                    },
                    "message": "Operation successful",
                },
            )
        return httpx.Response(500)

    client = _mock_client(_handler)
    auth = FuelFinderAuth(
        client,
        client_id=_secrets["client_id"],
        client_secret=_secrets["client_secret"],
    )
    fuel_finder = FuelFinderClient(client, auth)

    with caplog.at_level(logging.WARNING):
        _result = await fuel_finder.average_price_near(
            "SW1A 1AA", "E10", station_count=1
        )

    assert _result is FuelPriceFailure.STATIONS_UNAVAILABLE
    # Confirms the failure really was logged (not a vacuous pass from an
    # empty caplog) before checking none of it carries the secrets.
    assert any("500" in r.message for r in caplog.records)
    for _value in _secrets.values():
        assert not any(_value in r.message for r in caplog.records)
