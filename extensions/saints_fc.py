import asyncio
from datetime import date, datetime, timedelta
from logging import Logger, getLogger
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from common.constants import APP_NAME, TIMEZONE
from common.decorator import retry
from common.polling import every
from common.utils import is_null_or_empty
from hypervolt.led import THEMES_DIR, LedTheme, load_custom_effect

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
_UTC = ZoneInfo("UTC")
# TheSportsDB's ID space, not football-data.org's -- Southampton FC is 340 on
# football-data.org but 134778 on TheSportsDB.
_DEFAULT_TEAM_ID = 134778
# Fixture kick-off times firm up from TBD in the days before a match, so a
# once-an-hour check catches the update well before kick-off without leaning
# on the shared test key. Configurable for an operator on a personal key.
_DEFAULT_POLL_INTERVAL_HOURS = 1
# TheSportsDB's free tier needs no registration: "3" is a public, shared test
# key documented by TheSportsDB itself, embedded directly in the URL path
# rather than sent as a header. Kept as a config default (not hardcoded) so
# an operator can drop in a personal key later purely via config.yml.
_DEFAULT_API_KEY = "3"
_API_BASE_URL_TEMPLATE = "https://www.thesportsdb.com/api/v1/json/{api_key}"
_SAINTS_FC_THEME = "saints_fc.yaml"


def _kickoff_from_timestamp(raw: str) -> datetime | None:
    # TheSportsDB gives strTimestamp as "YYYY-MM-DD HH:MM:SS" in UTC with no
    # offset; a minority of records carry a full ISO offset instead. Attach
    # UTC to the former, respect the offset on the latter.
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC)
    except ValueError:
        pass
    try:
        _parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return _parsed if _parsed.tzinfo is not None else _parsed.replace(tzinfo=_UTC)


def _kickoff_from_local_fields(date_local: str, time_local: str) -> datetime | None:
    # dateEventLocal ("YYYY-MM-DD") + strTimeLocal ("HH:MM:SS"), attached to
    # the charger-local zone.
    try:
        return datetime.strptime(
            f"{date_local} {time_local}", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=_LOCAL_TZ)
    except ValueError:
        return None


def _parse_kickoff(event: dict[str, Any]) -> datetime | None:
    # Always returns an aware datetime, or None when no field yields one (the
    # date is still recorded, with an empty kick-off list).
    _timestamp = str(event.get("strTimestamp") or "").strip()
    if _timestamp:
        _kickoff = _kickoff_from_timestamp(_timestamp)
        if _kickoff is not None:
            return _kickoff
    _date_local = str(event.get("dateEventLocal") or "").strip()
    _time_local = str(event.get("strTimeLocal") or "").strip()
    if _date_local and _time_local:
        return _kickoff_from_local_fields(_date_local, _time_local)
    return None


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
        self._poll_interval_hours = config.get(
            "poll_interval_hours", _DEFAULT_POLL_INTERVAL_HOURS
        )
        if (
            isinstance(self._poll_interval_hours, bool)
            or not isinstance(self._poll_interval_hours, (int, float))
            or self._poll_interval_hours <= 0
        ):
            # ValueError, not TypeError, to match every other config
            # validation error in this extension (api_key) -- load_extensions()
            # catches them identically, so one consistent type is one less
            # thing for an operator reading logs to remember.
            raise ValueError(
                f"poll_interval_hours must be a positive number, got type "
                f"{type(self._poll_interval_hours).__name__}."
            )
        self._client = httpx.AsyncClient(
            base_url=_API_BASE_URL_TEMPLATE.format(api_key=self._api_key)
        )
        # Local match date -> timezone-aware kick-off instants. An empty list
        # means "match that day, kick-off time not yet known".
        self._matches: dict[date, list[datetime]] = {}
        self._task: asyncio.Task | None = None
        # Loaded once here (after the cheap config checks) so a missing or
        # malformed themes/saints_fc.yaml raises from __init__ -- load_extensions
        # then logs it and treats the extension as absent (ADR 0007).
        self._leds = load_custom_effect(THEMES_DIR / _SAINTS_FC_THEME)
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
        # the recurring interval task is scheduled -- without this, a same-day
        # deploy or restart would be blind to a match happening that same day
        # until the first interval tick. load_extensions() already awaits
        # start() and catches/logs any exception from it, so this blocking
        # check needs no error handling beyond what _check_and_record does.
        await self._check_and_record(datetime.now(_LOCAL_TZ).date())
        self._task = asyncio.create_task(
            every(self._poll_interval_hours * 3600, self._poll_once)
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
    async def _fetch_kickoffs_on_date(self, target_date: date) -> list[datetime] | None:
        # None distinguishes "no fixture that day" (leave _matches alone) from
        # "a fixture, here are its parseable kick-offs" (an empty list still
        # records the date). A failed fetch raises past @retry instead.
        _target_iso = target_date.isoformat()
        _next_events = await self._fetch_events("/eventsnext.php", "events")
        _last_events = await self._fetch_events("/eventslast.php", "results")
        _events_on_date = [
            _event
            for _event in _next_events + _last_events
            if _event.get("dateEventLocal") == _target_iso
        ]
        if not _events_on_date:
            return None
        return [
            _kickoff
            for _kickoff in (_parse_kickoff(_event) for _event in _events_on_date)
            if _kickoff is not None
        ]

    async def _check_and_record(self, target_date: date) -> None:
        try:
            _kickoffs = await self._fetch_kickoffs_on_date(target_date)
        except Exception as e:
            logger.warning(
                f"LED theme extension 'saints_fc' poll failed: {type(e).__name__}: {e}."
            )
            return
        if _kickoffs is not None:
            self._matches[target_date] = _kickoffs

    async def _poll_once(self) -> None:
        _today = datetime.now(_LOCAL_TZ).date()
        _tomorrow = _today + timedelta(days=1)
        await self._check_and_record(_today)
        await self._check_and_record(_tomorrow)
        # Drop dates now in the past so a long-running process's fixture store
        # can't grow without bound.
        self._matches = {d: v for d, v in self._matches.items() if d >= _today}

    async def resolve(self, now: datetime) -> LedTheme | None:
        _d = now.astimezone(_LOCAL_TZ).date()
        if _d not in self._matches:
            return None
        # The whole local match date at this slice -- the kick-off window
        # narrows this in issue #117. Charging-gated (always_on=False) until
        # then.
        return LedTheme(effect_name="saints_fc", leds=self._leds, always_on=False)
