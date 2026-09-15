import logging.config
from datetime import UTC, datetime, timedelta
from logging import Logger, getLogger

import httpx
from common.constants import APP_NAME
from common.exceptions import APIError
from common.logging import config

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


class FuelFinderAuth:
    def __init__(
        self, client: httpx.AsyncClient, client_id: str, client_secret: str
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        self._refresh_token: str | None = None
        self._refresh_token_expires_at: datetime | None = None

    def _token_still_valid(self) -> bool:
        return (
            self._access_token is not None
            and self._expires_at is not None
            and datetime.now(UTC) < self._expires_at
        )

    def _refresh_token_still_valid(self) -> bool:
        return (
            self._refresh_token is not None
            and self._refresh_token_expires_at is not None
            and datetime.now(UTC) < self._refresh_token_expires_at
        )

    async def _request_token(self, path: str, body: dict) -> dict:
        # An application-level failure (success: false, a missing "data" key,
        # or a non-JSON body) can still arrive as HTTP 200 -- wrapping every
        # failure mode as APIError, the same way app/octopus/client.py and
        # app/hypervolt/client/rest.py already do for their own API calls,
        # routes it through get_access_token()'s existing exception handling
        # instead of letting a raw KeyError/JSONDecodeError escape uncaught.
        try:
            _response = await self._client.post(url=path, json=body, timeout=10)
            _response.raise_for_status()
            _payload = _response.json()
            if not isinstance(_payload, dict) or not _payload.get("success"):
                raise APIError(_payload)
            _data: dict = _payload["data"]
        except Exception as e:
            if isinstance(e, APIError):
                raise
            raise APIError(f"Fuel Finder token request to {path} failed: {e}.") from e
        return _data

    async def _generate_access_token(self) -> str:
        _data = await self._request_token(
            "/api/v1/oauth/generate_access_token",
            {"client_id": self._client_id, "client_secret": self._client_secret},
        )
        _token = self._store_token(_data)
        self._store_refresh_token(_data)
        return _token

    async def _regenerate_access_token(self) -> str:
        _data = await self._request_token(
            "/api/v1/oauth/regenerate_access_token",
            {"client_id": self._client_id, "refresh_token": self._refresh_token},
        )
        # A confirmed sample response has no new refresh_token field -- the
        # refresh token doesn't rotate, so keep using the original one until
        # it hits its own refresh_token_expires_in.
        return self._store_token(_data)

    def _store_token(self, data: dict) -> str:
        _token = data["access_token"]
        self._access_token = _token
        # A 90% margin, not the nominal expiry -- matches the existing
        # Hypervolt REST client's own token caching
        # (app/hypervolt/client/rest.py's _update_tokens), so a token with
        # only a little real lifetime left isn't reused for a request that
        # then races expiry due to clock skew or network latency.
        self._expires_at = datetime.now(UTC) + timedelta(
            seconds=int(data["expires_in"] * 0.9)
        )
        return _token

    def _store_refresh_token(self, data: dict) -> None:
        self._refresh_token = data["refresh_token"]
        self._refresh_token_expires_at = datetime.now(UTC) + timedelta(
            seconds=data["refresh_token_expires_in"]
        )

    def invalidate(self) -> None:
        # Discards the cached access token so the next get_access_token()
        # call fetches a fresh one, even though local expires_at bookkeeping
        # still thinks it's in-date -- lets a caller recover from a token the
        # server rejected early (a live 401) rather than replaying the same
        # stale token forever.
        self._access_token = None
        self._expires_at = None

    async def get_access_token(self) -> str | None:
        if self._token_still_valid():
            return self._access_token
        if self._refresh_token_still_valid():
            try:
                return await self._regenerate_access_token()
            except APIError as e:
                # The refresh token can expire server-side (or be revoked)
                # even though our local bookkeeping still thinks it's
                # in-date -- fall back to a full re-auth rather than
                # surfacing a failure the caller can't act on.
                logger.warning(
                    f"Regenerating the Fuel Finder access token failed: "
                    f"{type(e).__name__}. Falling back to a full re-auth."
                )
        try:
            return await self._generate_access_token()
        except APIError as e:
            logger.warning(
                f"Fetching a Fuel Finder access token failed: {type(e).__name__}."
            )
            return None
