import asyncio
import logging.config
from datetime import datetime
from logging import Logger, getLogger
from zoneinfo import ZoneInfo

import httpx
from common.constants import APP_NAME, TIMEZONE
from common.logging import config
from common.polling import every
from hypervolt.led import LedTheme

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

_LOCAL_TZ = ZoneInfo(TIMEZONE)
_DEFAULT_TEAM_ID = 340
_DEFAULT_POLL_INTERVAL_SECS = 300
_API_BASE_URL = "https://api.football-data.org/v4"
_LED_COUNT = 51
_RED = {"r": 1.0, "g": 0.0, "b": 0.0}
_WHITE = {"r": 1.0, "g": 1.0, "b": 1.0}


def _matchday_leds() -> list[dict[str, float]]:
    return [dict(_RED if i % 2 == 0 else _WHITE) for i in range(_LED_COUNT)]


class SaintsFcExtension:
    def __init__(self, config: dict) -> None:
        self._api_key = config["api_key"]
        self._team_id = config.get("team_id", _DEFAULT_TEAM_ID)
        self._poll_interval_secs = config.get(
            "poll_interval_secs", _DEFAULT_POLL_INTERVAL_SECS
        )
        self._client = httpx.AsyncClient(
            base_url=_API_BASE_URL, headers={"X-Auth-Token": self._api_key}
        )
        self._has_match_today = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(
            every(self._poll_interval_secs, self._poll_once)
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()

    async def _poll_once(self) -> None:
        try:
            _today = datetime.now(_LOCAL_TZ).date().isoformat()
            _response = await self._client.get(
                f"/teams/{self._team_id}/matches",
                params={"dateFrom": _today, "dateTo": _today},
            )
            _response.raise_for_status()
            _data = _response.json()
            self._has_match_today = len(_data.get("matches", [])) > 0
        except Exception as e:
            logger.warning(
                f"LED theme extension 'saints_fc' poll failed: {type(e).__name__}: {e}."
            )

    async def resolve(self, now: datetime) -> LedTheme | None:
        if not self._has_match_today:
            return None
        return LedTheme(effect_name="saints_fc_matchday", leds=_matchday_leds())
