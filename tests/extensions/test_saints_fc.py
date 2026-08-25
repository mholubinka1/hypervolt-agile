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


async def test_resolve_returns_none_before_any_poll_has_happened() -> None:
    extension = SaintsFcExtension({"api_key": "test-key"})

    theme = await extension.resolve(_FIXED_NOW)

    assert theme is None


@pytest.mark.parametrize("poll_interval_secs", [0, -10, "not-a-number", True, False])
def test_init_rejects_a_non_positive_or_non_numeric_poll_interval(
    poll_interval_secs: object,
) -> None:
    # common.polling.every divides by delay in its scheduling math -- 0
    # crashes it outright, and a negative/non-numeric value produces
    # nonsensical or crashing scheduling. Reject at construction time so
    # load_extensions can skip the entry cleanly instead of the background
    # task crashing later.
    with pytest.raises(ValueError):
        SaintsFcExtension(
            {"api_key": "test-key", "poll_interval_secs": poll_interval_secs}
        )


def _mock_client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=handler, base_url="https://api.football-data.org/v4"
    )


async def test_poll_requests_the_correct_football_data_org_url() -> None:
    # Locks in httpx's base_url + leading-slash path resolution behaviour --
    # verified empirically against the installed httpx version that
    # `base_url="https://api.football-data.org/v4"` combined with a
    # leading-slash request path resolves correctly (preserves the /v4
    # prefix), so this uses the extension's own real base_url (not a test
    # double's), swapping in only the transport.
    _captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        _captured["url"] = str(request.url)
        return httpx.Response(200, json={"matches": []})

    extension = SaintsFcExtension({"api_key": "test-key", "team_id": 340})
    extension._client = _mock_client(httpx.MockTransport(_handler))

    await extension._poll_once()

    assert _captured["url"].startswith(
        "https://api.football-data.org/v4/teams/340/matches"
    )


async def test_a_match_today_response_produces_the_matchday_theme() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"matches": [{"id": 1}]})

    extension = SaintsFcExtension({"api_key": "test-key"})
    extension._client = _mock_client(httpx.MockTransport(_handler))

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


async def test_no_match_today_response_produces_none() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"matches": []})

    extension = SaintsFcExtension({"api_key": "test-key"})
    extension._client = _mock_client(httpx.MockTransport(_handler))

    await extension._poll_once()
    theme = await extension.resolve(_FIXED_NOW)

    assert theme is None


async def test_a_poll_failure_logs_a_warning_and_keeps_the_cached_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"matches": [{"id": 1}]})

    def _failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    extension = SaintsFcExtension({"api_key": "test-key"})
    extension._client = _mock_client(httpx.MockTransport(_ok_handler))
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
        return httpx.Response(200, json={"matches": [{"id": 1}]})

    extension = SaintsFcExtension({"api_key": "test-key"})
    extension._client = _mock_client(httpx.MockTransport(_handler))

    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("common.decorator.asyncio.sleep", AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        await extension._poll_once()

    theme = await extension.resolve(_FIXED_NOW)
    assert theme is not None
    assert _calls["count"] == 2
    # The retry succeeded within the retry budget, so saints_fc's own
    # poll-failed warning (logged only once retries are exhausted) must not
    # fire -- only common.decorator.retry's own per-attempt warning does.
    assert not any("poll failed" in r.message for r in caplog.records)


async def test_start_polls_in_the_background_without_the_caller_awaiting_it() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"matches": [{"id": 1}]})

    extension = SaintsFcExtension({"api_key": "test-key", "poll_interval_secs": 300})
    extension._client = _mock_client(httpx.MockTransport(_handler))

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension.start()
        for _ in range(10):
            await asyncio.sleep(0)
    theme = await extension.resolve(_FIXED_NOW)

    assert theme is not None
    await extension.stop()


async def test_stop_cancels_the_background_task_cleanly() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"matches": []})

    extension = SaintsFcExtension({"api_key": "test-key", "poll_interval_secs": 300})
    extension._client = _mock_client(httpx.MockTransport(_handler))
    await extension.start()
    for _ in range(10):
        await asyncio.sleep(0)

    await extension.stop()

    assert extension._task is not None
    assert extension._task.cancelled() or extension._task.done()
