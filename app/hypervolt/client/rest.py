from __future__ import annotations

import logging.config
from datetime import datetime, timedelta
from functools import wraps
from logging import Logger, getLogger
from typing import (
    Any,
    Callable,
    Dict,
    Optional,
    TypeVar,
)
from zoneinfo import ZoneInfo

from common.constants import APP_NAME
from common.decorator import retry
from common.exceptions import APIError, AuthenticationError
from common.logging import config
from hypervolt.model import HypervoltCharger
from requests import Session

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

# TODO: focus code to v3 only
R = TypeVar("R")


def requires_auth(method: Callable[..., R]) -> Callable[..., R]:
    @wraps(method)
    def wrapper(self: HypervoltRestClient, *args: Any, **kwargs: Any) -> R:
        if datetime.now(ZoneInfo("UTC")) >= self.access_token_expiry_time:
            self.authenticate()
        return method(self, *args, **kwargs)

    return wrapper


class HypervoltRestClient:
    _auth_url: str = (
        "https://kc.prod.hypervolt.co.uk/realms/retail-customers/protocol/openid-connect/token"
    )
    _base_url: str = "https://api.hypervolt.co.uk/charger"

    _username: str
    _password: str

    charger: HypervoltCharger

    access_token: str
    access_token_expiry_time: datetime
    _refresh_token: Optional[str] = None

    _session: Session

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password

        self._session = Session()
        self.authenticate()
        self.charger = self._get_chargers()

    def get_access_token(self) -> str:
        if datetime.now(ZoneInfo("UTC")) >= self.access_token_expiry_time:
            self.authenticate()
        return self.access_token

    # region Authentication

    @retry()
    def authenticate(self) -> None:
        if self._refresh_token:
            try:
                self._refresh_authenticate()
                return
            except Exception as e:
                logger.warning(
                    f"Unable to refresh Hypervolt API authentication tokens, re-authenticating: {str(e)}"
                )

        data = {
            "client_id": "home-assistant",
            "grant_type": "password",
            "scope": "openid profile email offline_access",
            "username": self._username,
            "password": self._password,
        }

        try:
            _response = self._session.post(self._auth_url, data=data)
            if _response.status_code != 200:
                _error = _response.json()
                raise APIError(_error)
            _response_json = _response.json()
            self._update_tokens(_response_json)
        except Exception as e:
            logger.error(f"Unable to authenticate with Hypervolt API: {e}")
            raise AuthenticationError(str(e)) from e

    @retry()
    def _refresh_authenticate(self) -> None:
        _now = datetime.now(ZoneInfo("UTC"))
        if self.access_token_expiry_time < _now:
            self.authenticate()
            return

        data = {
            "client_id": "home-assistant",
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        _response = self._session.post(self._auth_url, data=data)
        if _response.status_code != 200:
            _error = _response.json()
            raise APIError(_error)
        _response_json = _response.json()
        self._update_tokens(_response_json)

    def _update_tokens(self, response_json: Dict) -> None:
        self.access_token = response_json["access_token"]
        self._refresh_token = response_json["refresh_token"]

        _expires_in = response_json["expires_in"]
        self.access_token_expiry_time = datetime.now(ZoneInfo("UTC")) + timedelta(
            seconds=int(_expires_in * 0.9)
        )

        if self._session is None:
            raise RuntimeError("HTTP session not initialised.")
        self._session.headers["authorization"] = f"Bearer {self.access_token}"
        return

    # endregion

    # region Charger Information

    def _get_charger_major_version(self, charger_id: str) -> int:
        charger_id_hex = hex(int(charger_id))[2:]
        num_id_bytes = (len(charger_id_hex) + 1) // 2 * 2
        if num_id_bytes == 12:
            return 2
        elif num_id_bytes == 16:
            return 3
        raise NotImplementedError(f"Unrecognised charger ID format: {charger_id}")

    @retry()
    @requires_auth
    def _get_chargers(self) -> HypervoltCharger:
        _api_endpoint = f"{self._base_url}/by-owner"

        _response = self._session.get(_api_endpoint)
        if _response.status_code != 200:
            _error = _response.json()
            raise APIError(_error)

        _response_json = _response.json()
        _chargers = [
            HypervoltCharger(
                id=c["charger_id"],
                maj_version=self._get_charger_major_version(c["charger_id"]),
            )
            for c in _response_json["chargers"]
        ]

        if not _chargers:
            raise ValueError(
                "Unable to find chargers linked to this Hypervolt account."
            )

        if len(_chargers) > 1:
            logger.warning(
                "Found multiple linked chargers linked to this Hypervolt account, using only one."
            )

        _charger = _chargers[0]
        logger.info(
            f"Hypervolt Charger found. ID: {_charger.id}, v{_charger.maj_version}."
        )
        return _charger

    # endregion
