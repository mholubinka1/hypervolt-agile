from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
from hypervolt.led import load_extensions

from config import ExtensionEntry

_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
_LONDON = ZoneInfo("Europe/London")
_RealAsyncClient = httpx.AsyncClient


def _no_match_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"matches": []})

    kwargs["transport"] = httpx.MockTransport(_handler)
    return _RealAsyncClient(**kwargs)  # type: ignore[arg-type]


async def test_shipped_saints_fc_extension_loads_and_resolves_to_none_by_default() -> (
    None
):
    # start() launches a real background poll (per ADR 0005) -- patching
    # httpx.AsyncClient at construction time is the real network boundary, so
    # this exercises the actual production dynamic-import path (load_extensions
    # -> the real extensions/saints_fc.py file) without a live network call.
    entries = [ExtensionEntry(name="saints_fc", config={"api_key": "test-key"})]

    with patch("saints_fc.httpx.AsyncClient", side_effect=_no_match_client):
        result = await load_extensions(entries, _EXTENSIONS_DIR)

        assert len(result) == 1
        assert result[0].name == "saints_fc"
        theme = await result[0].resolve(datetime.now(tz=_LONDON))
        assert theme is None
        await result[0].stop()
