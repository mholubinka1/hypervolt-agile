import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from behaviours.dynamic_charging_threshold import (
    DynamicChargingThresholdExtension,
    _dynamic_threshold_incl_vat,
)
from fuel_finder.auth import FuelFinderAuth
from fuel_finder.client import FuelFinderClient

_FUEL_FINDER_BASE_URL = "https://www.fuel-finder.service.gov.uk"
_GEOCODE_RESULT = {
    "status": 200,
    "result": {"postcode": "SW1A 1AA", "latitude": 51.5, "longitude": -0.14},
}


def test_computes_the_dynamic_threshold_as_eighty_percent_of_the_fuel_breakeven_price() -> (
    None
):
    # 150p/litre petrol, 45.4609 mpg, 3.5 mi/kWh -> breakeven of 52.5p/kWh
    # (fuel cost per mile == electric cost per mile), margined down by the
    # fixed 20% safety margin to 42.0p/kWh.
    assert _dynamic_threshold_incl_vat(
        fuel_price_per_litre=150.0, mpg=45.4609, mi_per_kwh=3.5
    ) == pytest.approx(42.0)


_DEFAULT_UPDATE_EVERY_MINS = 30


def _valid_config(**overrides: Any) -> dict[str, Any]:
    # update_every_mins deliberately isn't a key here -- the extension no
    # longer reads it from config at all (it arrives as its own constructor
    # parameter, see _DEFAULT_UPDATE_EVERY_MINS), so it has no place among
    # the config fields this helper builds.
    _config: dict[str, Any] = {
        "fuel_type": "petrol",
        "mpg": 45.4609,
        "postcode": "SW1A 1AA",
        "station_count": 1,
        "client_id": "the-client-id",
        "client_secret": "the-client-secret",
    }
    _config.update(overrides)
    return _config


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
        "fuel_types": ["E10", "B7_STANDARD"],
    }


def _price_entry(node_id: str, prices: list[tuple[str, float]]) -> dict:
    return {
        "node_id": node_id,
        "trading_name": "TEST STATION",
        "fuel_prices": [
            {
                "fuel_type": _fuel_type,
                "price": _price,
                "price_last_updated": "2026-09-07T20:37:43.000Z",
                "price_change_effective_timestamp": "2026-09-07T20:37:43.000Z",
            }
            for _fuel_type, _price in prices
        ],
    }


def _router(
    *,
    pfs_batches: list[list[dict]],
    price_batches: list[list[dict]],
) -> Any:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json=_GEOCODE_RESULT)
        if request.url.path == "/api/v1/oauth/generate_access_token":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "access_token": "the-token",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "refresh_token": "the-refresh-token",
                        "refresh_token_expires_in": 172800,
                    },
                    "message": "Operation successful",
                },
            )
        if request.url.path == "/api/v1/pfs":
            _pages = pfs_batches
        elif request.url.path == "/api/v1/pfs/fuel-prices":
            _pages = price_batches
        else:
            raise AssertionError(f"Unexpected request: {request.url}")
        _batch = int(request.url.params["batch-number"])
        _page = _pages[_batch - 1] if _batch <= len(_pages) else []
        # httpx.Response(json=...) hardcodes allow_nan=False internally,
        # unlike stdlib json.dumps's own default -- building the body
        # manually lets a non-finite price (NaN/Infinity) round-trip
        # through this mock the same way a permissive real API response
        # could still produce one, so invalid-price tests exercise the
        # real chain rather than mocking FuelFinderClient directly.
        return httpx.Response(
            200,
            content=json.dumps(_page).encode(),
            headers={"content-type": "application/json"},
        )

    return _handler


def _wire_custom_transport(
    extension: DynamicChargingThresholdExtension,
    handler: Any,
) -> None:
    # A lower-level sibling of _wire_mock_transport below for tests that need
    # to simulate a specific failing leg of the chain (a bad geocode
    # response, or an HTTP failure on a specific path) rather than a
    # successful pfs/fuel-prices pagination -- mirrors the same
    # rebuild-everything-from-one-mock-client approach used by the malformed-
    # geocode test further down this file.
    _mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=_FUEL_FINDER_BASE_URL
    )
    extension._client = _mock_client
    extension._auth = FuelFinderAuth(
        _mock_client, client_id="the-client-id", client_secret="the-client-secret"
    )
    extension._fuel_finder = FuelFinderClient(_mock_client, extension._auth)


def _wire_mock_transport(
    extension: DynamicChargingThresholdExtension,
    *,
    pfs_batches: list[list[dict]],
    price_batches: list[list[dict]],
) -> None:
    # Mirrors tests/extensions/test_saints_fc.py's own convention of
    # re-pointing the extension's real httpx client at a MockTransport after
    # construction, extended one layer further here since this extension
    # owns a FuelFinderAuth + FuelFinderClient built from that client rather
    # than calling it directly -- both are rebuilt from the same mock client
    # so every real code path (geocoding, auth, pagination, distance
    # ranking) still runs for real, only the transport is faked.
    _mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            _router(pfs_batches=pfs_batches, price_batches=price_batches)
        ),
        base_url=_FUEL_FINDER_BASE_URL,
    )
    extension._client = _mock_client
    extension._auth = FuelFinderAuth(
        _mock_client, client_id="the-client-id", client_secret="the-client-secret"
    )
    extension._fuel_finder = FuelFinderClient(_mock_client, extension._auth)


async def test_petrol_fuel_type_resolves_to_the_fuel_finder_e10_code() -> None:
    # Given fuel_type: petrol, the extension must select the E10 price at a
    # station reporting both grades, not the diesel (B7_STANDARD) price also
    # present in the same fixture data -- proof it resolved the code
    # correctly, without reaching into a private attribute to check it
    # directly.
    extension = DynamicChargingThresholdExtension(
        _valid_config(fuel_type="petrol"), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 130.0), ("B7_STANDARD", 999.0)])]],
    )

    await extension._poll_once()

    assert await extension.get_threshold() == pytest.approx(
        _dynamic_threshold_incl_vat(130.0, 45.4609, 3.5)
    )


async def test_diesel_fuel_type_resolves_to_the_fuel_finder_b7_standard_code() -> None:
    # Mirrors the petrol test above: the station reports both grades, so
    # picking the diesel price specifically proves fuel_type: diesel
    # resolved to B7_STANDARD, not the petrol E10 price present in the same
    # fixture data.
    extension = DynamicChargingThresholdExtension(
        _valid_config(fuel_type="diesel"), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 999.0), ("B7_STANDARD", 140.0)])]],
    )

    await extension._poll_once()

    assert await extension.get_threshold() == pytest.approx(
        _dynamic_threshold_incl_vat(140.0, 45.4609, 3.5)
    )


def test_an_unrecognised_fuel_type_raises_value_error() -> None:
    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(
            _valid_config(fuel_type="lpg"),
            update_every_mins=_DEFAULT_UPDATE_EVERY_MINS,
        )


def test_a_non_string_unhashable_fuel_type_raises_value_error_not_type_error() -> None:
    # A YAML list (e.g. `fuel_type: [petrol]`) is unhashable -- must raise
    # the same actionable ValueError as any other invalid fuel_type, not a
    # TypeError from the dict membership check.
    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(
            _valid_config(fuel_type=["petrol"]),
            update_every_mins=_DEFAULT_UPDATE_EVERY_MINS,
        )


@pytest.mark.parametrize("bad_postcode", [None, "", "   ", 12345])
def test_postcode_must_be_a_non_blank_string(bad_postcode: object) -> None:
    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(
            _valid_config(postcode=bad_postcode),
            update_every_mins=_DEFAULT_UPDATE_EVERY_MINS,
        )


def test_a_missing_mpg_raises_value_error() -> None:
    _config = _valid_config()
    del _config["mpg"]
    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(
            _config, update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
        )


async def test_mi_per_kwh_defaults_to_three_point_five_when_omitted() -> None:
    # Observable through a poll's computed threshold, not by reaching into
    # a private attribute: the config omits mi_per_kwh entirely, and the
    # resulting cached threshold must match the default 3.5 mi/kWh.
    _config = _valid_config()
    assert "mi_per_kwh" not in _config
    extension = DynamicChargingThresholdExtension(
        _config, update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )

    await extension._poll_once()

    assert await extension.get_threshold() == pytest.approx(
        _dynamic_threshold_incl_vat(150.0, 45.4609, 3.5)
    )


def test_a_missing_station_count_raises_value_error() -> None:
    _config = _valid_config()
    del _config["station_count"]
    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(
            _config, update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
        )


@pytest.mark.parametrize("bad_mpg", [0, -10, "fast", float("nan"), float("inf"), True])
def test_mpg_must_be_a_positive_finite_number(bad_mpg: object) -> None:
    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(
            _valid_config(mpg=bad_mpg), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
        )


@pytest.mark.parametrize("bad_station_count", [0, -1, 1.5, "5", True])
def test_station_count_must_be_a_positive_int(bad_station_count: object) -> None:
    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(
            _valid_config(station_count=bad_station_count),
            update_every_mins=_DEFAULT_UPDATE_EVERY_MINS,
        )


@pytest.mark.parametrize(
    "bad_mi_per_kwh", [0, -3.5, "fast", float("nan"), float("inf"), True]
)
def test_mi_per_kwh_must_be_a_positive_finite_number_when_provided(
    bad_mi_per_kwh: object,
) -> None:
    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(
            _valid_config(mi_per_kwh=bad_mi_per_kwh),
            update_every_mins=_DEFAULT_UPDATE_EVERY_MINS,
        )


@pytest.mark.parametrize(
    "bad_radius_miles", [0, -10, "far", float("nan"), float("inf"), True]
)
def test_radius_miles_must_be_a_positive_finite_number_when_provided(
    bad_radius_miles: object,
) -> None:
    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(
            _valid_config(radius_miles=bad_radius_miles),
            update_every_mins=_DEFAULT_UPDATE_EVERY_MINS,
        )


# update_every_mins is no longer read from config at all -- it arrives as
# its own trusted constructor parameter (AppConfig.schedule.frequency,
# already validated by Pydantic), not a value read out of the operator's own
# free-form config dict, so there is no longer a "missing" or "invalid"
# config-shaped case to test here. See
# test_start_schedules_an_interval_poll_at_the_configured_cadence for
# coverage of the constructor parameter itself.


async def test_radius_miles_excludes_a_station_beyond_the_configured_radius() -> None:
    # Scenario: radius_miles is an upper-bound cap, not an alternative
    # selection mode -- a farther station reporting a cheaper price is
    # excluded outright, even though station_count would otherwise want more
    # matches. Proven by observing the computed threshold reflects only the
    # in-radius station's price, not an average blended with the excluded one.
    extension = DynamicChargingThresholdExtension(
        _valid_config(station_count=2, radius_miles=5),
        update_every_mins=_DEFAULT_UPDATE_EVERY_MINS,
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("near", 51.5, -0.14), _station("far", 52.5, -0.14)]],
        price_batches=[
            [
                _price_entry("near", [("E10", 150.0)]),
                _price_entry("far", [("E10", 100.0)]),
            ]
        ],
    )

    await extension._poll_once()

    assert await extension.get_threshold() == pytest.approx(
        _dynamic_threshold_incl_vat(150.0, 45.4609, 3.5)
    )


@pytest.mark.parametrize("credential_key", ["client_id", "client_secret"])
@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_a_missing_or_blank_credential_raises_value_error(
    credential_key: str, bad_value: object
) -> None:
    _config = _valid_config(**{credential_key: bad_value})

    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(
            _config, update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
        )


@pytest.mark.parametrize("credential_key", ["client_id", "client_secret"])
def test_a_non_string_credential_error_does_not_echo_the_value(
    credential_key: str,
) -> None:
    # A non-string, distinctive credential value: if the error message ever
    # echoed the value itself (rather than just its type), this fake but
    # secret-shaped value would show up verbatim -- mirrors saints_fc.py's
    # api_key validation convention (describe the type, don't echo).
    _config = _valid_config(**{credential_key: 424242424242})

    with pytest.raises(ValueError) as _exc_info:
        DynamicChargingThresholdExtension(
            _config, update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
        )

    assert "424242424242" not in str(_exc_info.value)
    assert "int" in str(_exc_info.value)


async def test_a_successful_poll_caches_the_computed_margined_threshold() -> None:
    # Scenario 9: the full real average_price_near() call chain (geocoding,
    # auth, pagination, distance ranking) runs against a MockTransport --
    # get_threshold() afterwards reflects the margined breakeven computed
    # from the price it found.
    extension = DynamicChargingThresholdExtension(
        _valid_config(mpg=45.4609, station_count=1),
        update_every_mins=_DEFAULT_UPDATE_EVERY_MINS,
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )

    assert await extension.get_threshold() is None  # nothing cached yet

    await extension._poll_once()

    assert await extension.get_threshold() == pytest.approx(42.0)


async def test_a_poll_finding_no_matching_fuel_type_clears_a_previously_cached_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 10: average_price_near() returns FuelPriceFailure.NO_MATCHING_STATION
    # (the only nearby station doesn't report the requested fuel type) --
    # get_threshold() must fall back cleanly to None afterward, not raise.
    # Seeds a real cached value from a prior successful poll first --
    # self._threshold starts at None by construction, so asserting None
    # after a no-match poll alone would pass whether or not a *previously
    # cached* value actually gets cleared. Also proves the warning log
    # states this failure's own distinct wording (postcode + fuel type),
    # not a generic message shared with the other three failure reasons.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    await extension._poll_once()
    assert await extension.get_threshold() is not None

    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("B7_STANDARD", 150.0)])]],
    )
    with caplog.at_level("WARNING"):
        await extension._poll_once()

    assert await extension.get_threshold() is None
    assert any(
        "found no fuel price near 'SW1A 1AA' for fuel type 'E10'" in r.message
        for r in caplog.records
    )
    assert not any("could not resolve postcode" in r.message for r in caplog.records)
    assert not any(
        "could not fetch the fuel station list" in r.message for r in caplog.records
    )
    assert not any("could not fetch fuel prices" in r.message for r in caplog.records)


async def test_a_poll_with_a_postcode_that_does_not_geocode_logs_the_geocode_specific_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # average_price_near() returns FuelPriceFailure.GEOCODE_FAILED when
    # postcodes.io 404s -- _clear_cache's log line must state this reason's
    # own wording (that the postcode itself couldn't be resolved), not the
    # NO_MATCHING_STATION wording ("found no fuel price...") that used to be
    # logged unconditionally for every failure before this change.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(404, json={"status": 404, "error": "not found"})
        raise AssertionError(f"Unexpected request: {request.url}")

    _wire_custom_transport(extension, _handler)

    with caplog.at_level("WARNING"):
        await extension._poll_once()

    assert await extension.get_threshold() is None
    assert any(
        "could not resolve postcode 'SW1A 1AA'" in r.message for r in caplog.records
    )
    assert not any("found no fuel price" in r.message for r in caplog.records)


async def test_a_poll_with_an_unavailable_station_list_logs_the_stations_specific_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # average_price_near() returns FuelPriceFailure.STATIONS_UNAVAILABLE when
    # the /api/v1/pfs fetch fails outright (geocoding and auth both succeed
    # here) -- _clear_cache's log line must state that the station list
    # itself couldn't be fetched, distinct from a price-list failure or a
    # genuine no-matching-station result.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json=_GEOCODE_RESULT)
        if request.url.path == "/api/v1/oauth/generate_access_token":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "access_token": "the-token",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "refresh_token": "the-refresh-token",
                        "refresh_token_expires_in": 172800,
                    },
                    "message": "Operation successful",
                },
            )
        if request.url.path == "/api/v1/pfs":
            return httpx.Response(500)
        raise AssertionError(f"Unexpected request: {request.url}")

    _wire_custom_transport(extension, _handler)

    with caplog.at_level("WARNING"):
        await extension._poll_once()

    assert await extension.get_threshold() is None
    assert any(
        "could not fetch the fuel station list" in r.message for r in caplog.records
    )
    assert not any("could not fetch fuel prices" in r.message for r in caplog.records)
    assert not any("found no fuel price" in r.message for r in caplog.records)


async def test_a_poll_with_an_unavailable_price_list_logs_the_prices_specific_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # average_price_near() returns FuelPriceFailure.PRICES_UNAVAILABLE when
    # the station list fetch succeeds but /api/v1/pfs/fuel-prices fails --
    # _clear_cache's log line must state that fuel prices specifically
    # couldn't be fetched, not the station-list wording used for the
    # opposite failure above.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json=_GEOCODE_RESULT)
        if request.url.path == "/api/v1/oauth/generate_access_token":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "access_token": "the-token",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "refresh_token": "the-refresh-token",
                        "refresh_token_expires_in": 172800,
                    },
                    "message": "Operation successful",
                },
            )
        if request.url.path == "/api/v1/pfs":
            return httpx.Response(
                200,
                content=json.dumps([_station("s1", 51.5, -0.14)]).encode(),
                headers={"content-type": "application/json"},
            )
        if request.url.path == "/api/v1/pfs/fuel-prices":
            return httpx.Response(500)
        raise AssertionError(f"Unexpected request: {request.url}")

    _wire_custom_transport(extension, _handler)

    with caplog.at_level("WARNING"):
        await extension._poll_once()

    assert await extension.get_threshold() is None
    assert any("could not fetch fuel prices" in r.message for r in caplog.records)
    assert not any(
        "could not fetch the fuel station list" in r.message for r in caplog.records
    )
    assert not any("found no fuel price" in r.message for r in caplog.records)


@pytest.mark.parametrize("bad_price", [-150.0, 0.0, float("nan"), float("inf")])
async def test_a_poll_receiving_an_invalid_fuel_price_clears_a_previously_cached_threshold(
    bad_price: float,
) -> None:
    # FuelFinderClient passes the API's raw price straight through with no
    # validation of its own -- a malformed payload (negative, zero, NaN, or
    # infinite) must be treated as unavailable data, the same as no price
    # found at all, rather than caching a threshold that silently breaks
    # every schedule comparison (NaN never compares true; infinity accepts
    # every price). Exercised through the real HTTP-mocked chain for every
    # case, including NaN/Infinity -- _router builds responses via raw
    # content= rather than httpx's stricter json= helper specifically so
    # these non-finite values can round-trip through it.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    await extension._poll_once()
    assert await extension.get_threshold() is not None

    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", bad_price)])]],
    )
    await extension._poll_once()

    assert await extension.get_threshold() is None


async def test_a_poll_computing_an_overflowing_threshold_clears_a_previously_cached_threshold() -> (
    None
):
    # A raw price can itself be finite and positive (passing the check
    # above) while still overflowing to inf once multiplied through the
    # breakeven formula -- 1e308p/litre is finite, but 1e308 * 4.54609
    # already exceeds float's max representable value. Must be treated as
    # unavailable data the same way an invalid raw price is, rather than
    # caching an infinite threshold that would accept every electricity
    # price.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    await extension._poll_once()
    assert await extension.get_threshold() is not None

    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 1e308)])]],
    )
    await extension._poll_once()

    assert await extension.get_threshold() is None


async def test_a_poll_computing_an_underflowing_threshold_clears_a_previously_cached_threshold() -> (
    None
):
    # The other edge of the same guard: an extreme but valid mpg can drive
    # the breakeven arithmetic to underflow to exactly 0.0 rather than
    # overflowing -- 1e-300p/litre and a 1e300 mpg (both individually
    # finite and positive, passing every earlier check) divide down past
    # float's smallest representable positive value. Must be treated as
    # unavailable data the same way an infinite threshold is, rather than
    # caching a zero threshold that would accept no electricity price at
    # all.
    extension = DynamicChargingThresholdExtension(
        _valid_config(mpg=1e300, station_count=1),
        update_every_mins=_DEFAULT_UPDATE_EVERY_MINS,
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    await extension._poll_once()
    assert await extension.get_threshold() is not None

    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 1e-300)])]],
    )
    await extension._poll_once()

    assert await extension.get_threshold() is None


async def test_a_poll_with_an_unchanged_price_does_not_log_at_info_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # An unchanged fuel price recomputes the identical threshold every
    # cadence tick -- must not repeat the info-level "computed threshold"
    # line for a value that hasn't moved, or the log fills up with
    # identical noise every 30 minutes.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    with caplog.at_level("INFO"):
        await extension._poll_once()
    assert any("computed threshold" in r.message for r in caplog.records)
    caplog.clear()

    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    with caplog.at_level("INFO"):
        await extension._poll_once()

    assert not any("computed threshold" in r.message for r in caplog.records)


async def test_a_poll_with_a_sub_cent_threshold_difference_does_not_log_at_info_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The suppression compares at the same 2dp precision the log line
    # itself displays, not exact float equality -- two prices close enough
    # that the computed threshold rounds to the same displayed value must
    # not re-log, even though the raw floats differ.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    await extension._poll_once()
    _threshold_before = await extension.get_threshold()
    caplog.clear()

    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.00001)])]],
    )
    with caplog.at_level("INFO"):
        await extension._poll_once()

    assert await extension.get_threshold() != _threshold_before  # raw float moved
    assert f"{_threshold_before:.2f}" == f"{await extension.get_threshold():.2f}"
    assert not any("computed threshold" in r.message for r in caplog.records)


async def test_a_poll_recomputing_the_same_threshold_after_a_cache_clear_logs_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A cache clear (e.g. a transient no-price-found poll) already logs its
    # own WARNING via _clear_cache -- once the cache is empty, the next
    # successful poll recovering the same numeric threshold as before the
    # clear is itself informative (proof the extension is working again)
    # and must log, not be suppressed as "unchanged".
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    await extension._poll_once()
    assert await extension.get_threshold() is not None

    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("B7_STANDARD", 150.0)])]],  # no E10 match
    )
    await extension._poll_once()
    assert await extension.get_threshold() is None
    caplog.clear()

    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    with caplog.at_level("INFO"):
        await extension._poll_once()

    assert any("computed threshold" in r.message for r in caplog.records)


async def test_a_poll_with_a_changed_price_logs_at_info_level_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The counterpart to the unchanged-price test above: a genuinely new
    # threshold must still be logged, proving the suppression is keyed on
    # the value actually changing, not a blanket silence after the first
    # poll.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    await extension._poll_once()
    caplog.clear()

    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 160.0)])]],
    )
    with caplog.at_level("INFO"):
        await extension._poll_once()

    assert any("computed threshold" in r.message for r in caplog.records)


async def test_get_threshold_returns_none_instantly_before_any_poll_has_completed() -> (
    None
):
    # Scenario 11: proves get_threshold() never awaits live I/O -- no
    # httpx.MockTransport (or any transport at all) is wired up, so any
    # attempt to actually make a request would hang or error; a near-zero
    # timeout on the await proves it returns immediately.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )

    _result = await asyncio.wait_for(extension.get_threshold(), timeout=0.01)

    assert _result is None


async def test_start_schedules_an_interval_poll_at_the_configured_cadence() -> None:
    # Scenario 12: mirrors tests/extensions/test_saints_fc.py's own cadence
    # test shape -- update_every_mins (minutes) converted to seconds for
    # every().
    extension = DynamicChargingThresholdExtension(_valid_config(), update_every_mins=15)

    with patch("behaviours.dynamic_charging_threshold.every", AsyncMock()) as _every:
        await extension.start()

    _every.assert_called_once_with(900, extension._poll_once)
    await extension.stop()


async def test_stop_cancels_the_background_task_and_closes_the_http_client() -> None:
    # Scenario 13: mirrors test_saints_fc.py's own stop() test -- the task
    # is cancelled cleanly and the extension's own httpx client is closed
    # (a closed client raises on any further use).
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    await extension.start()

    await extension.stop()

    assert extension._task is not None
    assert extension._task.cancelled() or extension._task.done()
    assert extension._client.is_closed


async def test_an_unexpected_error_during_a_poll_is_caught_and_preserves_the_cache(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 14: an unexpected failure -- here, a malformed 200 geocode
    # response missing the "result" key, which FuelFinderClient itself does
    # not catch (only httpx.HTTPError) -- must be caught inside the
    # extension itself, logged, and never propagate out of _poll_once()/
    # get_threshold(). A transient failure like this should not wipe a
    # still-valid cached threshold from a previous successful poll, so the
    # last good value is left in place rather than cleared to None.
    extension = DynamicChargingThresholdExtension(
        _valid_config(), update_every_mins=_DEFAULT_UPDATE_EVERY_MINS
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )
    await extension._poll_once()
    _cached_before = await extension.get_threshold()
    assert _cached_before is not None

    def _malformed_geocode_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.postcodes.io":
            return httpx.Response(200, json={"status": 200})  # no "result" key
        raise AssertionError(f"Unexpected request: {request.url}")

    extension._client = httpx.AsyncClient(
        transport=httpx.MockTransport(_malformed_geocode_handler),
        base_url=_FUEL_FINDER_BASE_URL,
    )
    extension._auth = FuelFinderAuth(
        extension._client, client_id="the-client-id", client_secret="the-client-secret"
    )
    extension._fuel_finder = FuelFinderClient(extension._client, extension._auth)

    with caplog.at_level("WARNING"):
        await extension._poll_once()  # must not raise

    assert await extension.get_threshold() == _cached_before
    assert any("poll failed unexpectedly" in r.message for r in caplog.records)
