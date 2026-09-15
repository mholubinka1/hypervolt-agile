import sys
from logging import getLogger
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

import httpx
from common.constants import APP_NAME
from common.logging import configure_file_logging
from schedule.behaviour import load_threshold_extension

from config import ExtensionEntry

_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
_RealAsyncClient = httpx.AsyncClient

_VALID_CONFIG = {
    "fuel_type": "petrol",
    "mpg": 45.4609,
    "postcode": "SW1A 1AA",
    "station_count": 5,
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
}


def _empty_dataset_router(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.postcodes.io":
        return httpx.Response(
            200, json={"result": {"latitude": 51.5, "longitude": -0.14}}
        )
    if request.url.path == "/api/v1/oauth/generate_access_token":
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "access_token": "test-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "test-refresh-token",
                    "refresh_token_expires_in": 172800,
                },
                "message": "Operation successful",
            },
        )
    return httpx.Response(200, json=[])  # empty page for /pfs and /pfs/fuel-prices


def _mock_async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
    kwargs["transport"] = httpx.MockTransport(_empty_dataset_router)
    return _RealAsyncClient(**kwargs)  # type: ignore[arg-type]


async def test_shipped_dynamic_charging_threshold_extension_loads_through_the_shared_loader() -> (
    None
):
    # Proves the shared, marker-method-parameterised loader
    # (app/common/extensions.py) actually discovers and wraps the real
    # production file at its shipped extensions/behaviours/ path (ADR 0021),
    # not just a synthetic fixture -- mirrors
    # tests/hypervolt/test_led_load_shipped_extensions.py's own precedent
    # for extensions/saints_fc.py.
    entry = ExtensionEntry(
        name="behaviours/dynamic_charging_threshold", config=_VALID_CONFIG
    )

    with patch(
        "behaviours.dynamic_charging_threshold.httpx.AsyncClient",
        side_effect=_mock_async_client,
    ):
        result = await load_threshold_extension(
            entry, _EXTENSIONS_DIR, update_every_mins=30
        )

        assert result is not None
        assert result.name == "behaviours/dynamic_charging_threshold"
        assert await result.invoke("get_threshold") is None  # nothing cached yet
        await result.stop()


async def test_loading_shipped_dynamic_charging_threshold_does_not_clobber_file_logging(
    tmp_path: Path,
) -> None:
    # main.py calls configure_file_logging() before loading any extension.
    # fuel_finder.auth/client are only ever reached through this dynamically
    # loaded extension (nothing in app/ imports them eagerly), so whether
    # their own module-level logging setup clobbers the file handler main.py
    # just added can only be proven by a genuinely fresh import here. Evict
    # them from sys.modules first: if an earlier-collected test file already
    # imported fuel_finder.auth/client (e.g. tests/fuel_finder/test_client.py),
    # Python's own import cache would otherwise skip their module-level code
    # entirely on this import, masking a real bug behind test collection
    # order.
    for _mod in ("fuel_finder.auth", "fuel_finder.client"):
        sys.modules.pop(_mod, None)
    configure_file_logging(str(tmp_path / "test.log"), "INFO")
    assert any(isinstance(h, RotatingFileHandler) for h in getLogger(APP_NAME).handlers)
    entry = ExtensionEntry(
        name="behaviours/dynamic_charging_threshold", config=_VALID_CONFIG
    )

    with patch(
        "behaviours.dynamic_charging_threshold.httpx.AsyncClient",
        side_effect=_mock_async_client,
    ):
        result = await load_threshold_extension(
            entry, _EXTENSIONS_DIR, update_every_mins=30
        )
        assert result is not None
        await result.stop()

    assert any(
        isinstance(h, RotatingFileHandler) for h in getLogger(APP_NAME).handlers
    ), "file handler was stripped by loading the extension"
