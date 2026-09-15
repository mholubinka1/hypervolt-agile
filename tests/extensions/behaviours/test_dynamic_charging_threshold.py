import asyncio
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


def _valid_config(**overrides: Any) -> dict[str, Any]:
    _config: dict[str, Any] = {
        "fuel_type": "petrol",
        "mpg": 45.4609,
        "postcode": "SW1A 1AA",
        "station_count": 1,
        "client_id": "the-client-id",
        "client_secret": "the-client-secret",
        "update_every_mins": 30,
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
        return httpx.Response(200, json=_page)

    return _handler


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
    extension = DynamicChargingThresholdExtension(_valid_config(fuel_type="petrol"))
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
    extension = DynamicChargingThresholdExtension(_valid_config(fuel_type="diesel"))
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
        DynamicChargingThresholdExtension(_valid_config(fuel_type="lpg"))


def test_a_missing_mpg_raises_value_error() -> None:
    _config = _valid_config()
    del _config["mpg"]
    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(_config)


async def test_mi_per_kwh_defaults_to_three_point_five_when_omitted() -> None:
    # Observable through a poll's computed threshold, not by reaching into
    # a private attribute: the config omits mi_per_kwh entirely, and the
    # resulting cached threshold must match the default 3.5 mi/kWh.
    _config = _valid_config()
    assert "mi_per_kwh" not in _config
    extension = DynamicChargingThresholdExtension(_config)
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
        DynamicChargingThresholdExtension(_config)


@pytest.mark.parametrize("credential_key", ["client_id", "client_secret"])
@pytest.mark.parametrize("bad_value", [None, "", "   "])
def test_a_missing_or_blank_credential_raises_value_error(
    credential_key: str, bad_value: object
) -> None:
    _config = _valid_config(**{credential_key: bad_value})

    with pytest.raises(ValueError):
        DynamicChargingThresholdExtension(_config)


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
        DynamicChargingThresholdExtension(_config)

    assert "424242424242" not in str(_exc_info.value)
    assert "int" in str(_exc_info.value)


async def test_a_successful_poll_caches_the_computed_margined_threshold() -> None:
    # Scenario 9: the full real average_price_near() call chain (geocoding,
    # auth, pagination, distance ranking) runs against a MockTransport --
    # get_threshold() afterwards reflects the margined breakeven computed
    # from the price it found.
    extension = DynamicChargingThresholdExtension(
        _valid_config(mpg=45.4609, station_count=1)
    )
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("E10", 150.0)])]],
    )

    assert await extension.get_threshold() is None  # nothing cached yet

    await extension._poll_once()

    assert await extension.get_threshold() == pytest.approx(42.0)


async def test_a_poll_finding_no_stations_leaves_get_threshold_returning_none() -> None:
    # Scenario 10: average_price_near() returns None (no station near the
    # postcode reports the requested fuel type) -- get_threshold() must
    # fall back cleanly to None afterward, not raise.
    extension = DynamicChargingThresholdExtension(_valid_config())
    _wire_mock_transport(
        extension,
        pfs_batches=[[_station("s1", 51.5, -0.14)]],
        price_batches=[[_price_entry("s1", [("B7_STANDARD", 150.0)])]],
    )

    await extension._poll_once()

    assert await extension.get_threshold() is None


async def test_get_threshold_returns_none_instantly_before_any_poll_has_completed() -> (
    None
):
    # Scenario 11: proves get_threshold() never awaits live I/O -- no
    # httpx.MockTransport (or any transport at all) is wired up, so any
    # attempt to actually make a request would hang or error; a near-zero
    # timeout on the await proves it returns immediately.
    extension = DynamicChargingThresholdExtension(_valid_config())

    _result = await asyncio.wait_for(extension.get_threshold(), timeout=0.01)

    assert _result is None


async def test_start_schedules_an_interval_poll_at_the_configured_cadence() -> None:
    # Scenario 12: mirrors tests/extensions/test_saints_fc.py's own cadence
    # test shape -- update_every_mins (minutes) converted to seconds for
    # every().
    extension = DynamicChargingThresholdExtension(_valid_config(update_every_mins=15))

    with patch("behaviours.dynamic_charging_threshold.every", AsyncMock()) as _every:
        await extension.start()

    _every.assert_called_once_with(900, extension._poll_once)
    await extension.stop()


async def test_stop_cancels_the_background_task_and_closes_the_http_client() -> None:
    # Scenario 13: mirrors test_saints_fc.py's own stop() test -- the task
    # is cancelled cleanly and the extension's own httpx client is closed
    # (a closed client raises on any further use).
    extension = DynamicChargingThresholdExtension(_valid_config())
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
    extension = DynamicChargingThresholdExtension(_valid_config())
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
