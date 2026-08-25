import asyncio
import logging.config
from datetime import date, datetime
from logging import Logger, getLogger
from zoneinfo import ZoneInfo

import httpx
from common.constants import APP_NAME, TIMEZONE
from common.decorator import retry
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
        self._match_date: date | None = None
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

    @retry()
    async def _fetch_has_match_today(self, today: date) -> bool:
        _response = await self._client.get(
            f"/teams/{self._team_id}/matches",
            params={"dateFrom": today.isoformat(), "dateTo": today.isoformat()},
        )
        _response.raise_for_status()
        _data = _response.json()
        return len(_data.get("matches", [])) > 0

    async def _poll_once(self) -> None:
        _today = datetime.now(_LOCAL_TZ).date()
        try:
            _has_match = await self._fetch_has_match_today(_today)
        except Exception as e:
            logger.warning(
                f"LED theme extension 'saints_fc' poll failed: {type(e).__name__}: {e}."
            )
            return
        self._match_date = _today if _has_match else None

    async def resolve(self, now: datetime) -> LedTheme | None:
        if (
            self._match_date is None
            or now.astimezone(_LOCAL_TZ).date() != self._match_date
        ):
            return None
        return LedTheme(effect_name="saints_fc_matchday", leds=_matchday_leds())
