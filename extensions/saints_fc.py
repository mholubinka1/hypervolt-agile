import asyncio
from datetime import date, datetime, timedelta
from logging import Logger, getLogger
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from common.constants import APP_NAME, TIMEZONE
from common.decorator import retry
from common.polling import daily_at
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
# Fixture schedules don't change minute-to-minute, so a once-a-day check is
# enough -- 23:00 is late enough that same-day fixture updates are unlikely,
# while still leaving a full day's notice before kickoff.
_DEFAULT_POLL_TIME = "23:00"
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


def _parse_poll_time(value: object) -> tuple[int, int]:
    # ValueError, not TypeError, to match every other config validation
    # error in this extension (api_key, formerly poll_interval_secs) -- all
    # are caught identically by load_extensions() regardless of type, so a
    # single consistent exception type is one less thing for an operator
    # reading logs to have to remember.
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004
            f"poll_time must be a string in HH:MM format, got type "
            f"{type(value).__name__}."
        )
    try:
        # Naive is fine here -- only the hour/minute fields are used, the
        # result is never compared as an actual instant.
        _parsed = datetime.strptime(value, "%H:%M")  # noqa: DTZ007
    except ValueError:
        raise ValueError(
            f"poll_time must be in HH:MM 24-hour format, got {value!r}."
        ) from None
    return _parsed.hour, _parsed.minute


class SaintsFcExtension:
    def __init__(self, config: dict[str, Any]) -> None:
        self._api_key = config.get("api_key", _DEFAULT_API_KEY)
        if not isinstance(self._api_key, str) or is_null_or_empty(self._api_key):
            # Describe the type, not the value -- api_key is a credential, so
            # it must never be echoed into a message a caller might log.
            raise ValueError(
                f"api_key must be a non-blank string, got type "
                f"{type(self._api_key).__name__}."
            )
        self._team_id = config.get("team_id", _DEFAULT_TEAM_ID)
        self._poll_hour, self._poll_minute = _parse_poll_time(
            config.get("poll_time", _DEFAULT_POLL_TIME)
        )
        self._client = httpx.AsyncClient(
            base_url=_API_BASE_URL_TEMPLATE.format(api_key=self._api_key)
        )
        self._match_dates: set[date] = set()
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
        # A bootstrap check for *today* specifically, awaited directly before
        # the recurring daily task is scheduled -- without this, a same-day
        # deploy or restart wouldn't discover a match happening that same
        # day, since every recurring poll from here on only ever checks
        # tomorrow. load_extensions() already awaits start() and already
        # catches/logs any exception from it, so this blocking check doesn't
        # need its own error handling beyond what _check_and_record does.
        await self._check_and_record(datetime.now(_LOCAL_TZ).date())
        self._task = asyncio.create_task(
            daily_at(self._poll_hour, self._poll_minute, _LOCAL_TZ, self._poll_once)
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
        # httpx's own exception messages embed the full request URL, and
        # TheSportsDB embeds api_key in that URL's path -- both branches
        # below re-raise with a sanitized message (no URL) so a personal key
        # never reaches common.decorator.retry's or _poll_once's logs. `from
        # e` is safe here (not a further leak risk): neither log site prints
        # a traceback or the exception chain, only str(e) of the exception
        # actually logged.
        try:
            _response = await self._client.get(path, params={"id": self._team_id})
            _response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise httpx.HTTPError(
                f"Request to {path} failed: "
                f"{e.response.status_code} {e.response.reason_phrase}."
            ) from e
        except httpx.HTTPError as e:
            raise httpx.HTTPError(
                f"Request to {path} failed: {type(e).__name__}."
            ) from e
        _events: list[dict[str, Any]] | None = _response.json().get(response_key)
        return _events or []

    @retry()
    async def _fetch_has_match_on_date(self, target_date: date) -> bool:
        _target_iso = target_date.isoformat()
        _next_events = await self._fetch_events("/eventsnext.php", "events")
        _last_events = await self._fetch_events("/eventslast.php", "results")
        return any(
            _event.get("dateEventLocal") == _target_iso
            for _event in _next_events + _last_events
        )

    async def _check_and_record(self, target_date: date) -> None:
        try:
            _has_match = await self._fetch_has_match_on_date(target_date)
        except Exception as e:
            logger.warning(
                f"LED theme extension 'saints_fc' poll failed: {type(e).__name__}: {e}."
            )
            return
        if _has_match:
            self._match_dates.add(target_date)

    async def _poll_once(self) -> None:
        _tomorrow = datetime.now(_LOCAL_TZ).date() + timedelta(days=1)
        await self._check_and_record(_tomorrow)

    async def resolve(self, now: datetime) -> LedTheme | None:
        if now.astimezone(_LOCAL_TZ).date() not in self._match_dates:
            return None
        return LedTheme(effect_name="saints_fc_matchday", leds=_matchday_leds())
