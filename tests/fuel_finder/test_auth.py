import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import httpx
from fuel_finder.auth import FuelFinderAuth

_TOKEN_RESPONSE = {
    "success": True,
    "data": {
        "access_token": "the-access-token",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "the-refresh-token",
        "refresh_token_expires_in": 172800,
    },
    "message": "Operation successful",
}


_REGENERATE_RESPONSE = {
    "success": True,
    "data": {
        "access_token": "the-regenerated-access-token",
        "token_type": "Bearer",
        "expires_in": 3600,
    },
    "message": "Operation successful",
}

_FIXED_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _mock_client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=handler, base_url="https://www.fuel-finder.service.gov.uk"
    )


def _frozen_clock(instant: datetime) -> Mock:
    # get_access_token()'s validity checks read datetime.now(timezone.utc)
    # internally -- freezing it lets a test make "the cached token is
    # expired" true without a real 1-hour sleep. Mirrors
    # tests/extensions/test_saints_fc.py's patch of saints_fc.datetime.
    _clock = Mock(wraps=datetime)
    _clock.now.return_value = instant
    return _clock


async def test_get_access_token_posts_client_credentials_and_returns_the_token() -> (
    None
):
    _requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        _requests.append(request)
        return httpx.Response(200, json=_TOKEN_RESPONSE)

    client = _mock_client(httpx.MockTransport(_handler))
    auth = FuelFinderAuth(client, client_id="my-client-id", client_secret="my-secret")

    token = await auth.get_access_token()

    assert token == "the-access-token"
    assert len(_requests) == 1
    assert _requests[0].url.path == "/api/v1/oauth/generate_access_token"
    _body = json.loads(_requests[0].content)
    assert _body == {"client_id": "my-client-id", "client_secret": "my-secret"}


async def test_get_access_token_reuses_a_still_valid_cached_token() -> None:
    # Scenario 2: a token already fetched and still within its validity window
    # must not trigger a second HTTP call -- the API's own guidance is to
    # reuse a cached token rather than fetch a fresh one per call.
    _requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        _requests.append(request)
        return httpx.Response(200, json=_TOKEN_RESPONSE)

    client = _mock_client(httpx.MockTransport(_handler))
    auth = FuelFinderAuth(client, client_id="my-client-id", client_secret="my-secret")

    _first_token = await auth.get_access_token()
    _second_token = await auth.get_access_token()

    assert _first_token == _second_token == "the-access-token"
    assert len(_requests) == 1


async def test_get_access_token_regenerates_via_refresh_token_once_expired() -> None:
    # Scenario 3: the cached access token has passed its expires_at but the
    # refresh token is still within refresh_token_expires_in -- regenerate,
    # not a full re-auth.
    _requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        _requests.append(request)
        if request.url.path == "/api/v1/oauth/generate_access_token":
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        return httpx.Response(200, json=_REGENERATE_RESPONSE)

    client = _mock_client(httpx.MockTransport(_handler))
    auth = FuelFinderAuth(client, client_id="my-client-id", client_secret="my-secret")

    with patch("fuel_finder.auth.datetime", _frozen_clock(_FIXED_NOW)):
        await auth.get_access_token()

    with patch(
        "fuel_finder.auth.datetime",
        _frozen_clock(_FIXED_NOW + timedelta(hours=2)),
    ):
        _token = await auth.get_access_token()

    assert _token == "the-regenerated-access-token"
    assert len(_requests) == 2
    assert _requests[1].url.path == "/api/v1/oauth/regenerate_access_token"
    _body = json.loads(_requests[1].content)
    assert _body == {
        "client_id": "my-client-id",
        "refresh_token": "the-refresh-token",
    }


async def test_get_access_token_falls_back_to_full_reauth_when_regenerate_fails() -> (
    None
):
    # Scenario 4: regenerate_access_token itself fails (e.g. 401 because the
    # refresh token expired server-side even though our local bookkeeping
    # still thought it was in-date) -- fall back to a full generate_access_token
    # call rather than surfacing the failure.
    _requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        _requests.append(request)
        if request.url.path == "/api/v1/oauth/regenerate_access_token":
            return httpx.Response(401, json={"success": False, "message": "expired"})
        return httpx.Response(200, json=_TOKEN_RESPONSE)

    client = _mock_client(httpx.MockTransport(_handler))
    auth = FuelFinderAuth(client, client_id="my-client-id", client_secret="my-secret")

    with patch("fuel_finder.auth.datetime", _frozen_clock(_FIXED_NOW)):
        await auth.get_access_token()

    with patch(
        "fuel_finder.auth.datetime",
        _frozen_clock(_FIXED_NOW + timedelta(hours=2)),
    ):
        _token = await auth.get_access_token()

    assert _token == "the-access-token"
    assert len(_requests) == 3
    assert _requests[1].url.path == "/api/v1/oauth/regenerate_access_token"
    assert _requests[2].url.path == "/api/v1/oauth/generate_access_token"


async def test_get_access_token_returns_none_when_no_cached_token_and_generate_fails() -> (
    None
):
    # Given no cached token at all (the very first call) and the full
    # generate_access_token call itself fails -- get_access_token() must
    # report unavailable, not raise.
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _mock_client(httpx.MockTransport(_handler))
    auth = FuelFinderAuth(client, client_id="my-client-id", client_secret="my-secret")

    token = await auth.get_access_token()

    assert token is None


async def test_get_access_token_returns_none_when_a_200_response_carries_no_data() -> (
    None
):
    # An application-level failure (success: false, or a missing "data" key)
    # can still arrive as HTTP 200 -- must not raise KeyError/TypeError past
    # get_access_token()'s own APIError handling (PR #163 review).
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"success": False, "message": "invalid credentials"}
        )

    client = _mock_client(httpx.MockTransport(_handler))
    auth = FuelFinderAuth(client, client_id="my-client-id", client_secret="my-secret")

    token = await auth.get_access_token()

    assert token is None


async def test_get_access_token_returns_none_when_the_response_body_is_not_json() -> (
    None
):
    # A genuinely non-JSON 200 body raises json.JSONDecodeError (a ValueError,
    # not an httpx exception) -- must be caught the same way as any other
    # request failure, not escape uncaught (PR #163 review).
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = _mock_client(httpx.MockTransport(_handler))
    auth = FuelFinderAuth(client, client_id="my-client-id", client_secret="my-secret")

    token = await auth.get_access_token()

    assert token is None
