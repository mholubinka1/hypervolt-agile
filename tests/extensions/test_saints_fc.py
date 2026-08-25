import asyncio
import logging
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from saints_fc import SaintsFcExtension

_LONDON = ZoneInfo("Europe/London")
_FIXED_NOW = datetime(2026, 8, 25, 12, 0, tzinfo=_LONDON)


def _frozen_clock() -> Mock:
    # _poll_once() reads datetime.now(_LOCAL_TZ) internally to compute
    # "today" for the API request and the cache key -- without freezing it,
    # a test asserting resolve() against a hardcoded date only passes on
    # that exact calendar day.
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


async def test_poll_uses_the_default_api_key_and_team_id_when_omitted() -> None:
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


async def test_poll_computes_today_in_europe_london_not_utc() -> None:
    # dateEventLocal must be compared against London's calendar date, not
    # UTC's -- a bare datetime.now() or datetime.now(timezone.utc) would
    # silently compute the wrong "today" for part of every day.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=None, last_events=None))
    _clock = _frozen_clock()

    with patch("saints_fc.datetime", _clock):
        await extension._poll_once()

    _clock.now.assert_called_once_with(_LONDON)


async def test_resolve_uses_londons_date_for_a_now_expressed_in_utc() -> None:
    # BST is UTC+1 -- 2026-08-24T23:30 UTC is 2026-08-25 00:30 in London, a
    # different calendar date. resolve() must convert `now` to London before
    # comparing dates, not use the UTC date directly.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": "2026-08-25"}], last_events=None)
    )
    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()
    _utc_just_before_london_midnight = datetime(
        2026, 8, 24, 23, 30, tzinfo=ZoneInfo("UTC")
    )

    theme = await extension.resolve(_utc_just_before_london_midnight)

    assert theme is not None
    assert theme.effect_name == "saints_fc_matchday"


async def test_resolve_returns_none_before_any_poll_has_happened() -> None:
    extension = SaintsFcExtension({})

    theme = await extension.resolve(_FIXED_NOW)

    assert theme is None


async def test_an_eventsnext_response_with_todays_date_produces_the_matchday_theme() -> (
    None
):
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": "2026-08-25"}], last_events=None)
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()
    theme = await extension.resolve(_FIXED_NOW)

    assert theme is not None
    assert theme.effect_name == "saints_fc_matchday"


async def test_no_todays_events_in_either_endpoint_produces_none() -> None:
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(
            next_events=[{"dateEventLocal": "2026-08-29"}],
            last_events=[{"dateEventLocal": "2026-08-22"}],
        )
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()
    theme = await extension.resolve(_FIXED_NOW)

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


async def test_an_eventslast_response_with_todays_date_also_produces_the_matchday_theme() -> (
    None
):
    # eventsnext stops showing a fixture once it's no longer "upcoming" --
    # this is what actually prompted the switch away from football-data.org:
    # a match already underway or finished today must still count.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=None, last_events=[{"dateEventLocal": "2026-08-25"}])
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()
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


@pytest.mark.parametrize(
    "poll_interval_secs",
    [0, -10, "not-a-number", True, False, float("nan"), float("inf")],
)
def test_init_rejects_a_non_positive_or_non_numeric_poll_interval(
    poll_interval_secs: object,
) -> None:
    # common.polling.every divides by delay in its scheduling math -- 0
    # crashes it outright, and a negative/non-numeric value produces
    # nonsensical or crashing scheduling. Reject at construction time so
    # load_extensions can skip the entry cleanly instead of the background
    # task crashing later.
    with pytest.raises(ValueError):
        SaintsFcExtension({"poll_interval_secs": poll_interval_secs})


@pytest.mark.parametrize("api_key", ["", "   "])
def test_init_rejects_a_blank_api_key(api_key: str) -> None:
    # An explicitly blank value (as opposed to omitting the key, which
    # defaults to the free shared key) would otherwise retry against
    # TheSportsDB with an invalid token on every poll.
    with pytest.raises(ValueError):
        SaintsFcExtension({"api_key": api_key})


async def test_a_poll_failure_logs_a_warning_and_keeps_the_cached_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": "2026-08-25"}], last_events=None)
    )
    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

        extension._client = _mock_client(httpx.MockTransport(_failing_handler))
        with (
            patch("common.decorator.asyncio.sleep", AsyncMock()),
            caplog.at_level(logging.WARNING),
        ):
            await extension._poll_once()

    theme = await extension.resolve(_FIXED_NOW)
    assert theme is not None
    assert theme.effect_name == "saints_fc_matchday"
    # common.decorator.retry logs its own warning per retry attempt (already
    # covered by its own tests) -- what matters here is exactly one warning
    # from saints_fc itself, once retries are exhausted.
    _own_records = [r for r in caplog.records if "poll failed" in r.message]
    assert len(_own_records) == 1


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
                200, json={"events": [{"dateEventLocal": "2026-08-25"}]}
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

    theme = await extension.resolve(_FIXED_NOW)
    assert theme is not None
    # 3 calls: the first eventsnext attempt fails, then the whole
    # _fetch_has_match_today (both eventsnext and eventslast) is retried
    # once and succeeds.
    assert _calls["count"] == 3
    # The retry succeeded within the retry budget, so saints_fc's own
    # poll-failed warning (logged only once retries are exhausted) must not
    # fire -- only common.decorator.retry's own per-attempt warning does.
    assert not any("poll failed" in r.message for r in caplog.records)


async def test_start_polls_in_the_background_without_the_caller_awaiting_it() -> None:
    extension = SaintsFcExtension({"poll_interval_secs": 300})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": "2026-08-25"}], last_events=None)
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension.start()
        for _ in range(10):
            await asyncio.sleep(0)
    theme = await extension.resolve(_FIXED_NOW)

    assert theme is not None
    await extension.stop()


async def test_stop_cancels_the_background_task_cleanly() -> None:
    extension = SaintsFcExtension({"poll_interval_secs": 300})
    extension._client = _mock_client(_router(next_events=None, last_events=None))
    await extension.start()
    for _ in range(10):
        await asyncio.sleep(0)

    await extension.stop()

    assert extension._task is not None
    assert extension._task.cancelled() or extension._task.done()
