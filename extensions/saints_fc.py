import asyncio
import math
from datetime import date, datetime
from logging import Logger, getLogger
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from common.constants import APP_NAME, TIMEZONE
from common.decorator import retry
from common.polling import every
from common.utils import is_null_or_empty
from hypervolt.led import LedTheme

# Deliberately does NOT call logging.config.dictConfig() -- unlike app/*
# modules (imported once, early, before main.py's configure_file_logging()
# runs), this module is loaded dynamically at runtime via load_extensions(),
# which happens AFTER configure_file_logging() has already configured the
# APP_NAME logger with a file handler. Re-running dictConfig here would
# reset it back to console-only, silently breaking file logging app-wide the
# moment any extension is loaded. The logger is already fully configured by
# the time this module executes -- just look it up.
logger: Logger = getLogger(APP_NAME)

_LOCAL_TZ = ZoneInfo(TIMEZONE)
# TheSportsDB's ID space, not football-data.org's -- Southampton FC is 340 on
# football-data.org but 134778 on TheSportsDB.
_DEFAULT_TEAM_ID = 134778
_DEFAULT_POLL_INTERVAL_SECS = 300
# TheSportsDB's free tier needs no registration: "3" is a public, shared test
# key documented by TheSportsDB itself, embedded directly in the URL path
# rather than sent as a header. Kept as a config default (not hardcoded) so
# an operator can drop in a personal key later purely via config.yml.
_DEFAULT_API_KEY = "3"
_API_BASE_URL_TEMPLATE = "https://www.thesportsdb.com/api/v1/json/{api_key}"
_LED_COUNT = 51
_RED = {"r": 1.0, "g": 0.0, "b": 0.0}
_WHITE = {"r": 1.0, "g": 1.0, "b": 1.0}


def _matchday_leds() -> list[dict[str, float]]:
    return [dict(_RED if i % 2 == 0 else _WHITE) for i in range(_LED_COUNT)]


class SaintsFcExtension:
    def __init__(self, config: dict[str, Any]) -> None:
        self._api_key = config.get("api_key", _DEFAULT_API_KEY)
        if is_null_or_empty(self._api_key):
            raise ValueError("api_key must not be blank.")
        self._team_id = config.get("team_id", _DEFAULT_TEAM_ID)
        self._poll_interval_secs = config.get(
            "poll_interval_secs", _DEFAULT_POLL_INTERVAL_SECS
        )
        if (
            isinstance(self._poll_interval_secs, bool)
            or not isinstance(self._poll_interval_secs, (int, float))
            or not math.isfinite(self._poll_interval_secs)
            or self._poll_interval_secs <= 0
        ):
            raise ValueError(
                f"poll_interval_secs must be a positive number, got "
                f"{self._poll_interval_secs!r}."
            )
        self._client = httpx.AsyncClient(
            base_url=_API_BASE_URL_TEMPLATE.format(api_key=self._api_key)
        )
        self._match_date: date | None = None
        self._task: asyncio.Task | None = None
        # team_id's default changed meaning between providers (340 on
        # football-data.org's ID space, 134778 on TheSportsDB's) -- there's
        # no fixed ID format to validate an explicit override against, so a
        # stale/wrong config value would otherwise silently track the wrong
        # club. Logging the resolved value at least makes it visible.
        logger.info(
            f"LED theme extension 'saints_fc' tracking TheSportsDB team_id {self._team_id}."
        )

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

    async def _fetch_events(self, path: str, response_key: str) -> list[dict[str, Any]]:
        _response = await self._client.get(path, params={"id": self._team_id})
        _response.raise_for_status()
        _events: list[dict[str, Any]] | None = _response.json().get(response_key)
        return _events or []

    @retry()
    async def _fetch_has_match_today(self, today: date) -> bool:
        _today_iso = today.isoformat()
        _next_events = await self._fetch_events("/eventsnext.php", "events")
        _last_events = await self._fetch_events("/eventslast.php", "results")
        return any(
            _event.get("dateEventLocal") == _today_iso
            for _event in _next_events + _last_events
        )

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
