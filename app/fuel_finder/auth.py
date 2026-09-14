import logging.config
from datetime import datetime, timedelta, UTC
from logging import Logger, getLogger

import httpx
from common.constants import APP_NAME
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
        _response = await self._client.post(url=path, json=body, timeout=10)
        _response.raise_for_status()
        _data: dict = _response.json()["data"]
        return _data

    async def _generate_access_token(self) -> str:
        _data = await self._request_token(
            "/api/v1/oauth/generate_access_token",
            {"client_id": self._client_id, "client_secret": self._client_secret},
        )
        self._store_token(_data)
        self._store_refresh_token(_data)
        return self._access_token  # type: ignore[return-value]

    async def _regenerate_access_token(self) -> str:
        _data = await self._request_token(
            "/api/v1/oauth/regenerate_access_token",
            {"client_id": self._client_id, "refresh_token": self._refresh_token},
        )
        self._store_token(_data)
        # A confirmed sample response has no new refresh_token field -- the
        # refresh token doesn't rotate, so keep using the original one until
        # it hits its own refresh_token_expires_in.
        return self._access_token  # type: ignore[return-value]

    def _store_token(self, data: dict) -> None:
        self._access_token = data["access_token"]
        self._expires_at = datetime.now(UTC) + timedelta(
            seconds=data["expires_in"]
        )

    def _store_refresh_token(self, data: dict) -> None:
        self._refresh_token = data["refresh_token"]
        self._refresh_token_expires_at = datetime.now(UTC) + timedelta(
            seconds=data["refresh_token_expires_in"]
        )

    def invalidate(self) -> None:
        """Discard the cached access token so the next get_access_token()
        call fetches a fresh one, even though our local expires_at bookkeeping
        still thinks it's in-date. Lets a caller recover from a token the
        server rejected early (a live 401) rather than replaying the same
        stale token forever."""
        self._access_token = None
        self._expires_at = None

    async def get_access_token(self) -> str | None:
        if self._token_still_valid():
            return self._access_token
        if self._refresh_token_still_valid():
            try:
                return await self._regenerate_access_token()
            except httpx.HTTPError as e:
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
        except httpx.HTTPError as e:
            logger.warning(
                f"Fetching a Fuel Finder access token failed: {type(e).__name__}."
            )
            return None
