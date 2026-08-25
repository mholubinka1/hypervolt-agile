import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from saints_fc import SaintsFcExtension

_LONDON = ZoneInfo("Europe/London")
_FIXED_NOW = datetime(2026, 8, 25, 12, 0, tzinfo=_LONDON)
_TODAY_ISO = "2026-08-25"
_TOMORROW_ISO = "2026-08-26"


def _frozen_clock() -> Mock:
    # _poll_once()/start() read datetime.now(_LOCAL_TZ) internally to compute
    # "today"/"tomorrow" -- without freezing it, a test asserting resolve()
    # against a hardcoded date only passes on that exact calendar day.
    _clock = Mock(wraps=datetime)
    _clock.now.return_value = _FIXED_NOW
    return _clock


def _mock_client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=handler, base_url="https://www.thesportsdb.com/api/v1/json/3"
    )


def _router(
    *, next_events: list[dict] | None = None, last_events: list[dict] | None = None
) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        if "eventsnext.php" in str(request.url):
            return httpx.Response(200, json={"events": next_events})
        if "eventslast.php" in str(request.url):
            return httpx.Response(200, json={"results": last_events})
        raise AssertionError(f"Unexpected request: {request.url}")

    return httpx.MockTransport(_handler)


def test_init_logs_the_resolved_team_id(caplog: pytest.LogCaptureFixture) -> None:
    # team_id's default silently changed meaning (340 on football-data.org's
    # ID space vs 134778 on TheSportsDB's) -- an operator's existing config
    # carrying over an old explicit team_id would otherwise silently track
    # the wrong club with no error, since there's no fixed ID format to
    # validate against. Logging the resolved value makes a stale/wrong
    # config visible in logs instead.
    with caplog.at_level(logging.INFO):
        SaintsFcExtension({"team_id": 999})

    assert any("999" in r.message for r in caplog.records)


async def test_poll_once_uses_the_default_api_key_and_team_id_when_omitted() -> None:
    _captured_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        _captured_urls.append(str(request.url))
        if "eventsnext.php" in str(request.url):
            return httpx.Response(200, json={"events": None})
        return httpx.Response(200, json={"results": None})

    extension = SaintsFcExtension({})
    # Re-point the extension's own real client (built from its own defaults
    # in __init__, base_url included) at the mock transport, rather than
    # asserting against a test double's hardcoded base_url -- this is what
    # actually proves __init__ built the URL from the default api_key.
    extension._client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url=extension._client.base_url
    )

    await extension._poll_once()

    assert len(_captured_urls) == 2
    assert all(
        url.startswith("https://www.thesportsdb.com/api/v1/json/3/")
        for url in _captured_urls
    )
    assert any("id=134778" in url for url in _captured_urls)


async def test_poll_once_computes_tomorrow_in_europe_london_not_utc() -> None:
    # dateEventLocal must be compared against London's calendar date, not
    # UTC's -- a bare datetime.now() or datetime.now(timezone.utc) would
    # silently compute the wrong "tomorrow" for part of every day.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=None, last_events=None))
    _clock = _frozen_clock()

    with patch("saints_fc.datetime", _clock):
        await extension._poll_once()

    _clock.now.assert_called_once_with(_LONDON)


async def test_poll_once_checks_tomorrow_not_today() -> None:
    # The recurring daily poll must check *tomorrow*'s date, not today's --
    # by the time today arrives, a match must already have been confirmed
    # the day before (or via the startup bootstrap check in start()).
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": _TOMORROW_ISO}], last_events=None)
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    _tomorrow_theme = await extension.resolve(_FIXED_NOW + timedelta(days=1))
    assert _tomorrow_theme is not None
    assert _tomorrow_theme.effect_name == "saints_fc_matchday"
    _today_theme = await extension.resolve(_FIXED_NOW)
    assert _today_theme is None


async def test_poll_once_finds_no_match_for_tomorrow_produces_none() -> None:
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(
            next_events=[{"dateEventLocal": "2026-08-30"}],
            last_events=[{"dateEventLocal": "2026-08-22"}],
        )
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()
    theme = await extension.resolve(_FIXED_NOW + timedelta(days=1))

    assert theme is None


async def test_both_endpoints_returning_null_is_treated_as_no_fixtures() -> None:
    # TheSportsDB returns "events": null / "results": null (not []) when a
    # team has no fixtures in that window -- must not crash on the None.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=None, last_events=None))

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()
    theme = await extension.resolve(_FIXED_NOW)

    assert theme is None


async def test_resolve_uses_londons_date_for_a_now_expressed_in_utc() -> None:
    # BST is UTC+1 -- 2026-08-25T23:30 UTC is 2026-08-26 00:30 in London, a
    # different calendar date. resolve() must convert `now` to London before
    # comparing dates, not use the UTC date directly.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": _TOMORROW_ISO}], last_events=None)
    )
    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()
    _utc_just_before_londons_tomorrow_midnight = datetime(
        2026, 8, 25, 23, 30, tzinfo=ZoneInfo("UTC")
    )

    theme = await extension.resolve(_utc_just_before_londons_tomorrow_midnight)

    assert theme is not None
    assert theme.effect_name == "saints_fc_matchday"


async def test_resolve_returns_none_before_any_poll_has_happened() -> None:
    extension = SaintsFcExtension({})

    theme = await extension.resolve(_FIXED_NOW)

    assert theme is None


async def test_start_directly_confirms_a_match_today_before_returning() -> None:
    # start() awaits its bootstrap "today" check directly -- unlike the
    # recurring daily task, no yielding to the event loop is needed for the
    # caller to see today's confirmed match reflected in resolve().
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": _TODAY_ISO}], last_events=None)
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension.start()
    theme = await extension.resolve(_FIXED_NOW)

    assert theme is not None
    assert theme.effect_name == "saints_fc_matchday"
    assert theme.leds is not None
    assert len(theme.leds) == 51
    _colours = {tuple(sorted(led.items())) for led in theme.leds}
    assert _colours == {
        (("b", 0.0), ("g", 0.0), ("r", 1.0)),
        (("b", 1.0), ("g", 1.0), ("r", 1.0)),
    }, "expected exactly Southampton FC red and white"
    assert theme.leds[0] != theme.leds[1], "adjacent LEDs must alternate"
    await extension.stop()


async def test_start_bootstrap_check_finds_a_match_today_via_eventslast() -> None:
    # eventsnext stops showing a fixture once it's no longer "upcoming" --
    # this is what prompted the original switch away from football-data.org:
    # a match already underway or finished today must still count. The
    # bootstrap "today" check in start() is the only path that can ever see
    # this via eventslast, since the recurring daily poll only ever checks
    # tomorrow (a future date can never be "recently completed").
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=None, last_events=[{"dateEventLocal": _TODAY_ISO}])
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension.start()
    theme = await extension.resolve(_FIXED_NOW)

    assert theme is not None
    assert theme.effect_name == "saints_fc_matchday"
    await extension.stop()


async def test_start_schedules_the_daily_poll_at_the_default_time() -> None:
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=None, last_events=None))

    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("saints_fc.daily_at", AsyncMock()) as _daily_at,
    ):
        await extension.start()

    _daily_at.assert_called_once_with(23, 0, _LONDON, extension._poll_once)
    await extension.stop()


async def test_start_schedules_the_daily_poll_at_a_configured_poll_time() -> None:
    extension = SaintsFcExtension({"poll_time": "06:30"})
    extension._client = _mock_client(_router(next_events=None, last_events=None))

    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("saints_fc.daily_at", AsyncMock()) as _daily_at,
    ):
        await extension.start()

    _daily_at.assert_called_once_with(6, 30, _LONDON, extension._poll_once)
    await extension.stop()


async def test_stop_cancels_the_background_task_cleanly() -> None:
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=None, last_events=None))

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension.start()

    await extension.stop()

    assert extension._task is not None
    assert extension._task.cancelled() or extension._task.done()


@pytest.mark.parametrize("poll_time", ["24:00", "23:60", "not-a-time", "11pm", ""])
def test_init_rejects_an_invalid_poll_time_string(poll_time: str) -> None:
    with pytest.raises(ValueError):
        SaintsFcExtension({"poll_time": poll_time})


@pytest.mark.parametrize("poll_time", [23, 23.0, True, None, ["23:00"]])
def test_init_rejects_a_non_string_poll_time(poll_time: object) -> None:
    with pytest.raises(ValueError):
        SaintsFcExtension({"poll_time": poll_time})


@pytest.mark.parametrize("api_key", ["", "   "])
def test_init_rejects_a_blank_api_key(api_key: str) -> None:
    # An explicitly blank value (as opposed to omitting the key, which
    # defaults to the free shared key) would otherwise retry against
    # TheSportsDB with an invalid token on every poll.
    with pytest.raises(ValueError):
        SaintsFcExtension({"api_key": api_key})


@pytest.mark.parametrize("api_key", [3, 3.5, True, ["3"], None])
def test_init_rejects_a_non_string_api_key(api_key: object) -> None:
    # An unquoted numeric value in YAML (e.g. `api_key: 3`) parses as an int,
    # not a str -- is_null_or_empty() assumes a str and calls .strip() on it,
    # so without this check the extension would fail to load with a
    # confusing AttributeError instead of this clean ValueError. None covers
    # a bare `api_key:` line with no value, the most realistic real-world
    # trigger.
    with pytest.raises(ValueError):
        SaintsFcExtension({"api_key": api_key})


async def test_a_poll_failure_logs_a_warning_and_keeps_previously_confirmed_dates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": _TODAY_ISO}], last_events=None)
    )
    with patch("saints_fc.datetime", _frozen_clock()):
        await extension.start()  # bootstrap check confirms today

        extension._client = _mock_client(httpx.MockTransport(_failing_handler))
        with (
            patch("common.decorator.asyncio.sleep", AsyncMock()),
            caplog.at_level(logging.WARNING),
        ):
            await extension._poll_once()  # tomorrow's check fails

    theme = await extension.resolve(_FIXED_NOW)
    assert theme is not None
    assert theme.effect_name == "saints_fc_matchday"
    # common.decorator.retry logs its own warning per retry attempt (already
    # covered by its own tests) -- what matters here is exactly one warning
    # from saints_fc itself, once retries are exhausted.
    _own_records = [r for r in caplog.records if "poll failed" in r.message]
    assert len(_own_records) == 1
    await extension.stop()


async def test_a_poll_failure_does_not_leak_the_api_key_into_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # httpx.HTTPStatusError's default message embeds the full request URL,
    # and TheSportsDB's URL embeds api_key in its path -- a poll failure must
    # not let that raw URL reach the logs, or an operator's personal key
    # (not just the public "3") would end up written to the log file.
    def _failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    extension = SaintsFcExtension({"api_key": "MY-SECRET-KEY"})
    extension._client = httpx.AsyncClient(
        transport=httpx.MockTransport(_failing_handler),
        base_url=extension._client.base_url,
    )

    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("common.decorator.asyncio.sleep", AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        await extension._poll_once()

    assert not any("MY-SECRET-KEY" in r.message for r in caplog.records)
    # The status code itself isn't secret and remains useful for triage --
    # only the URL (and the key embedded in it) needed to be stripped.
    assert any("500" in r.message for r in caplog.records)


async def test_a_single_transient_failure_is_retried_and_does_not_log_a_saints_fc_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _calls = {"count": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        _calls["count"] += 1
        if _calls["count"] == 1:
            return httpx.Response(500)
        if "eventsnext.php" in str(request.url):
            return httpx.Response(
                200, json={"events": [{"dateEventLocal": _TOMORROW_ISO}]}
            )
        return httpx.Response(200, json={"results": None})

    extension = SaintsFcExtension({})
    extension._client = _mock_client(httpx.MockTransport(_handler))

    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("common.decorator.asyncio.sleep", AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        await extension._poll_once()

    theme = await extension.resolve(_FIXED_NOW + timedelta(days=1))
    assert theme is not None
    # 3 calls: the first eventsnext attempt fails, then the whole
    # _fetch_has_match_on_date (both eventsnext and eventslast) is retried
    # once and succeeds.
    assert _calls["count"] == 3
    # The retry succeeded within the retry budget, so saints_fc's own
    # poll-failed warning (logged only once retries are exhausted) must not
    # fire -- only common.decorator.retry's own per-attempt warning does.
    assert not any("poll failed" in r.message for r in caplog.records)
