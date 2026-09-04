import logging
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest
from saints_fc import SaintsFcExtension, _kickoff_from_timestamp

_LONDON = ZoneInfo("Europe/London")
_UTC = ZoneInfo("UTC")
_FIXED_NOW = datetime(2026, 8, 25, 12, 0, tzinfo=_LONDON)
_TODAY = date(2026, 8, 25)
_TOMORROW = date(2026, 8, 26)
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


def test_init_raises_when_the_shipped_colour_map_cannot_be_loaded() -> None:
    # Scenario 3: a missing/malformed themes/saints_fc.yaml must raise from
    # __init__ so load_extensions() logs it and treats the extension as
    # absent (ADR 0007) rather than the app failing to start.
    with (
        patch("saints_fc.load_custom_effect", side_effect=FileNotFoundError("gone")),
        pytest.raises(FileNotFoundError),
    ):
        SaintsFcExtension({})


def test_init_checks_config_before_touching_the_colour_map() -> None:
    # The cheap config validation fails fast even if the colour file is also
    # unloadable -- an operator sees the config mistake first.
    with (
        patch("saints_fc.load_custom_effect", side_effect=FileNotFoundError("gone")),
        pytest.raises(ValueError),
    ):
        SaintsFcExtension({"api_key": ""})


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

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    # Two endpoints (eventsnext + eventslast), checked for both today and
    # tomorrow -> four requests per poll.
    assert len(_captured_urls) == 4
    assert all(
        url.startswith("https://www.thesportsdb.com/api/v1/json/3/")
        for url in _captured_urls
    )
    assert any("id=134778" in url for url in _captured_urls)


async def test_poll_once_computes_today_in_europe_london_not_utc() -> None:
    # dateEventLocal must be compared against London's calendar date, not
    # UTC's -- a bare datetime.now() or datetime.now(timezone.utc) would
    # silently compute the wrong "today"/"tomorrow" for part of every day.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=None, last_events=None))
    _clock = _frozen_clock()

    with patch("saints_fc.datetime", _clock):
        await extension._poll_once()

    _clock.now.assert_called_once_with(_LONDON)


async def test_poll_once_records_both_today_and_tomorrow() -> None:
    # Scenario 11: each poll checks *both* dates itself, so a fixture that
    # firms up during the day is picked up without waiting for the next
    # calendar day's poll.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(
            next_events=[
                {"dateEventLocal": _TODAY_ISO},
                {"dateEventLocal": _TOMORROW_ISO},
            ],
            last_events=None,
        )
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    assert _TODAY in extension._matches
    assert _TOMORROW in extension._matches


async def test_poll_once_prunes_dates_now_in_the_past() -> None:
    # Scenario 12: fixes the fixture store growing without bound in a
    # long-running process -- a poll drops keys for dates before today.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": _TOMORROW_ISO}], last_events=None)
    )
    extension._matches[date(2026, 8, 20)] = []
    extension._matches[_TODAY] = []

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    assert date(2026, 8, 20) not in extension._matches
    assert _TODAY in extension._matches
    assert _TOMORROW in extension._matches


async def test_poll_once_finds_no_match_produces_none() -> None:
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(
            next_events=[{"dateEventLocal": "2026-08-30"}],
            last_events=[{"dateEventLocal": "2026-08-22"}],
        )
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()
    _now = _FIXED_NOW + timedelta(days=1)

    assert (await extension.resolve(_now)) is None
    assert (await extension.resolve_fallback(_now)) is None


async def test_both_endpoints_returning_null_is_treated_as_no_fixtures() -> None:
    # TheSportsDB returns "events": null / "results": null (not []) when a
    # team has no fixtures in that window -- must not crash on the None.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=None, last_events=None))

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    assert (await extension.resolve(_FIXED_NOW)) is None
    assert (await extension.resolve_fallback(_FIXED_NOW)) is None


async def test_a_confirmed_fixture_records_its_utc_timestamp_as_an_aware_instant() -> (
    None
):
    # Scenario 4: strTimestamp is "YYYY-MM-DD HH:MM:SS" UTC (no offset). The
    # store maps the local date to a list holding an aware datetime equal to
    # that instant.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(
            next_events=[
                {
                    "dateEventLocal": _TOMORROW_ISO,
                    "strTimestamp": "2026-08-26 14:00:00",
                }
            ],
            last_events=None,
        )
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    _kickoffs = extension._matches[_TOMORROW]
    assert _kickoffs == [datetime(2026, 8, 26, 14, 0, tzinfo=_UTC)]
    assert _kickoffs[0].tzinfo is not None


async def test_a_fixture_with_an_iso_offset_timestamp_is_respected() -> None:
    # Scenario 4: some records carry an explicit offset -- respect it rather
    # than forcing UTC.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(
            next_events=[
                {
                    "dateEventLocal": _TOMORROW_ISO,
                    "strTimestamp": "2026-08-26T14:00:00+00:00",
                }
            ],
            last_events=None,
        )
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    assert extension._matches[_TOMORROW] == [datetime(2026, 8, 26, 14, 0, tzinfo=_UTC)]


async def test_resolve_lights_the_always_on_strip_only_inside_the_match_window() -> (
    None
):
    # Tracer bullet for issue #117: from 30 minutes before kick-off until three
    # hours after, the strip is always-on; outside that, resolve() yields
    # nothing (the charging-gated fallback is a separate path).
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(
            next_events=[
                {"dateEventLocal": _TODAY_ISO, "strTimestamp": "2026-08-25 15:00:00"}
            ],
            last_events=None,
        )
    )
    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    _in_window = await extension.resolve(datetime(2026, 8, 25, 15, 30, tzinfo=_UTC))
    _before_leadin = await extension.resolve(datetime(2026, 8, 25, 14, 0, tzinfo=_UTC))

    assert _in_window is not None
    assert _in_window.effect_name == "saints_fc"
    assert _in_window.always_on is True
    assert _before_leadin is None


async def _seeded(matches: dict[date, list[datetime]]) -> SaintsFcExtension:
    # Reach-in seeding of the fixture store, matching this file's existing
    # style (see test_poll_once_prunes_dates_now_in_the_past) -- lets a test
    # pin an exact kick-off instant without routing it through the poll/parse
    # path, which has its own coverage.
    extension = SaintsFcExtension({})
    extension._matches = matches
    return extension


async def test_resolve_is_none_one_second_after_the_window_closes() -> None:
    # Scenario 2: KO+3h is the inclusive upper bound; one second past it is out.
    _ko = datetime(2026, 8, 25, 15, 0, tzinfo=_UTC)
    extension = await _seeded({date(2026, 8, 25): [_ko]})

    _at_close = await extension.resolve(_ko + timedelta(hours=3))
    _just_after = await extension.resolve(_ko + timedelta(hours=3, seconds=1))

    assert _at_close is not None
    assert _just_after is None


async def test_resolve_lights_the_strip_at_the_exact_inclusive_bounds() -> None:
    # Scenario 3: exactly KO-30m and exactly KO+3h are both inside the window.
    _ko = datetime(2026, 8, 25, 15, 0, tzinfo=_UTC)
    extension = await _seeded({date(2026, 8, 25): [_ko]})

    _at_leadin = await extension.resolve(_ko - timedelta(minutes=30))
    _at_close = await extension.resolve(_ko + timedelta(hours=3))

    assert _at_leadin is not None
    assert _at_leadin.always_on is True
    assert _at_close is not None
    assert _at_close.always_on is True


async def test_resolve_covers_either_fixtures_window_in_a_double_header() -> None:
    # Scenario 4: two kick-offs on one local date -- the strip shows during
    # either window (their union) and goes dark in the gap between them.
    _early_ko = datetime(2026, 8, 25, 12, 0, tzinfo=_UTC)
    _late_ko = datetime(2026, 8, 25, 17, 30, tzinfo=_UTC)
    extension = await _seeded({date(2026, 8, 25): [_early_ko, _late_ko]})

    _in_first = await extension.resolve(datetime(2026, 8, 25, 12, 15, tzinfo=_UTC))
    _in_second = await extension.resolve(datetime(2026, 8, 25, 18, 0, tzinfo=_UTC))
    _in_gap = await extension.resolve(datetime(2026, 8, 25, 16, 0, tzinfo=_UTC))

    assert _in_first is not None
    assert _in_second is not None
    assert _in_gap is None


async def test_unknown_kickoff_is_fallback_only_all_day() -> None:
    # Scenario 5: an empty kick-off list contributes no window -- resolve()
    # returns None for every `now` that day, and resolve_fallback() offers the
    # charging-gated strip.
    extension = await _seeded({date(2026, 8, 25): []})

    for _hour in (0, 6, 12, 15, 21, 23):
        _now = datetime(2026, 8, 25, _hour, 0, tzinfo=_LONDON)
        assert (await extension.resolve(_now)) is None
        _fallback = await extension.resolve_fallback(_now)
        assert _fallback is not None
        assert _fallback.effect_name == "saints_fc"
        assert _fallback.always_on is False


async def test_resolve_fallback_offers_the_strip_outside_a_known_window() -> None:
    # Scenario 6: match date, known kick-off, `now` before KO-30m and after
    # KO+3h -- resolve_fallback() returns the charging-gated strip.
    _ko = datetime(2026, 8, 25, 15, 0, tzinfo=_UTC)
    extension = await _seeded({date(2026, 8, 25): [_ko]})

    _before = await extension.resolve_fallback(_ko - timedelta(hours=1))
    _after = await extension.resolve_fallback(_ko + timedelta(hours=4))

    for _theme in (_before, _after):
        assert _theme is not None
        assert _theme.effect_name == "saints_fc"
        assert _theme.always_on is False


async def test_resolve_fallback_is_none_inside_the_window() -> None:
    # Scenario 7: resolve() owns the in-window case; resolve_fallback() stays
    # out of its way so the strip is never offered at both priorities at once.
    _ko = datetime(2026, 8, 25, 15, 0, tzinfo=_UTC)
    extension = await _seeded({date(2026, 8, 25): [_ko]})

    assert (await extension.resolve_fallback(_ko)) is None
    assert (await extension.resolve_fallback(_ko - timedelta(minutes=30))) is None
    assert (await extension.resolve_fallback(_ko + timedelta(hours=3))) is None


async def test_resolve_fallback_is_none_on_a_non_match_date() -> None:
    # Scenario 8: nothing recorded for the date -> the extension contributes
    # nothing on either pass.
    extension = await _seeded(
        {date(2026, 8, 25): [datetime(2026, 8, 25, 15, 0, tzinfo=_UTC)]}
    )

    _other_day = datetime(2026, 8, 26, 15, 0, tzinfo=_UTC)
    assert (await extension.resolve(_other_day)) is None
    assert (await extension.resolve_fallback(_other_day)) is None


async def test_window_bounds_hold_across_a_dst_transition() -> None:
    # Scenario 9: BST->GMT is 2026-10-25 02:00 local. A kick-off at 01:00 UTC
    # (02:00 BST) has KO+3h at 04:00 UTC == 04:00 GMT -- the window is 3.5h of
    # real elapsed time regardless of the clock going back an hour mid-window.
    _ko = _kickoff_from_timestamp("2026-10-25 01:00:00")
    assert _ko is not None
    extension = await _seeded({date(2026, 10, 25): [_ko]})

    _in_window = await extension.resolve(datetime(2026, 10, 25, 3, 30, tzinfo=_UTC))
    _after_window = await extension.resolve(datetime(2026, 10, 25, 4, 30, tzinfo=_UTC))

    assert _in_window is not None
    assert _in_window.always_on is True
    assert _after_window is None


async def test_kickoff_falls_back_to_local_time_and_local_date_fields() -> None:
    # Scenario 5: no strTimestamp -> combine strTimeLocal + dateEventLocal
    # and attach the charger-local timezone.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(
            next_events=[
                {
                    "dateEventLocal": _TOMORROW_ISO,
                    "strTimestamp": "",
                    "strTimeLocal": "15:00:00",
                }
            ],
            last_events=None,
        )
    )

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    assert extension._matches[_TOMORROW] == [
        datetime(2026, 8, 26, 15, 0, tzinfo=_LONDON)
    ]


@pytest.mark.parametrize(
    "event",
    [
        {"dateEventLocal": _TOMORROW_ISO},
        {"dateEventLocal": _TOMORROW_ISO, "strTimestamp": "TBD"},
        {"dateEventLocal": _TOMORROW_ISO, "strTimestamp": "", "strTimeLocal": ""},
        {"dateEventLocal": _TOMORROW_ISO, "strTimeLocal": "not-a-time"},
    ],
)
async def test_a_fixture_with_no_parseable_kickoff_records_an_empty_list(
    event: dict,
) -> None:
    # Scenario 6: the date is still recorded (an empty list means "match that
    # day, kick-off unknown").
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=[event], last_events=None))

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    assert extension._matches[_TOMORROW] == []


@pytest.mark.parametrize(
    "event",
    [
        {"dateEventLocal": _TOMORROW_ISO},
        {"dateEventLocal": _TOMORROW_ISO, "strTimestamp": "2026-08-26 14:00:00"},
    ],
)
async def test_outside_every_window_the_strip_is_a_charging_gated_fallback(
    event: dict,
) -> None:
    # Scenario 6: on a match date but well away from any kick-off (or with the
    # kick-off unknown), resolve() yields nothing and resolve_fallback() offers
    # the charging-gated (always_on=False) strip.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=[event], last_events=None))

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()

    _early = datetime(2026, 8, 26, 6, 0, tzinfo=_LONDON)
    _late = datetime(2026, 8, 26, 23, 0, tzinfo=_LONDON)
    for _now in (_early, _late):
        assert (await extension.resolve(_now)) is None
        _fallback = await extension.resolve_fallback(_now)
        assert _fallback is not None
        assert _fallback.effect_name == "saints_fc"
        assert _fallback.always_on is False


async def test_the_date_lookup_uses_londons_date_for_a_now_expressed_in_utc() -> None:
    # BST is UTC+1 -- 2026-08-25T23:30 UTC is 2026-08-26 00:30 in London, a
    # different calendar date. The date lookup must convert `now` to London
    # before comparing, not use the UTC date directly.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": _TOMORROW_ISO}], last_events=None)
    )
    with patch("saints_fc.datetime", _frozen_clock()):
        await extension._poll_once()
    _utc_just_before_londons_tomorrow_midnight = datetime(
        2026, 8, 25, 23, 30, tzinfo=_UTC
    )

    theme = await extension.resolve_fallback(_utc_just_before_londons_tomorrow_midnight)

    assert theme is not None
    assert theme.effect_name == "saints_fc"


async def test_resolve_returns_none_before_any_poll_has_happened() -> None:
    extension = SaintsFcExtension({})

    assert (await extension.resolve(_FIXED_NOW)) is None
    assert (await extension.resolve_fallback(_FIXED_NOW)) is None


async def test_resolve_fallback_uses_the_shipped_colour_map_as_a_charging_gated_theme() -> (
    None
):
    # Tracer bullet for issue #116, now the "whole match day, kick-off unknown"
    # behaviour: the strip is the tuned themes/saints_fc.yaml colour map (not a
    # placeholder alternation), wired as effect_name "saints_fc", and
    # charging-gated (always_on False) -- offered by resolve_fallback since the
    # match window (issue #117) now owns resolve().
    from hypervolt.led import THEMES_DIR, load_custom_effect

    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": _TODAY_ISO}], last_events=None)
    )

    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("saints_fc.every", AsyncMock()),
    ):
        await extension.start()
    theme = await extension.resolve_fallback(_FIXED_NOW)
    await extension.stop()

    assert theme is not None
    assert theme.effect_name == "saints_fc"
    assert theme.always_on is False
    assert theme.leds == load_custom_effect(THEMES_DIR / "saints_fc.yaml")


async def test_start_directly_confirms_a_match_today_before_returning() -> None:
    # start() awaits its bootstrap "today" check directly -- unlike the
    # recurring interval task, no yielding to the event loop is needed for the
    # caller to see today's confirmed match reflected in resolve().
    from hypervolt.led import THEMES_DIR, load_custom_effect

    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": _TODAY_ISO}], last_events=None)
    )

    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("saints_fc.every", AsyncMock()),
    ):
        await extension.start()
    theme = await extension.resolve_fallback(_FIXED_NOW)

    assert theme is not None
    assert theme.effect_name == "saints_fc"
    assert theme.leds == load_custom_effect(THEMES_DIR / "saints_fc.yaml")
    await extension.stop()


async def test_start_bootstrap_check_finds_a_match_today_via_eventslast() -> None:
    # eventsnext stops showing a fixture once it's no longer "upcoming" --
    # this is what prompted the original switch away from football-data.org:
    # a match already underway or finished today must still count. The
    # bootstrap "today" check in start() is the only path that can ever see
    # this via eventslast, since the recurring poll checks today+tomorrow and
    # a future date can never be "recently completed".
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=None, last_events=[{"dateEventLocal": _TODAY_ISO}])
    )

    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("saints_fc.every", AsyncMock()),
    ):
        await extension.start()
    theme = await extension.resolve_fallback(_FIXED_NOW)

    assert theme is not None
    assert theme.effect_name == "saints_fc"
    await extension.stop()


async def test_resolve_returns_the_theme_for_both_a_bootstrap_and_a_later_confirmed_date() -> (
    None
):
    # The whole reason _match_dates (now _matches) is keyed by date: a
    # bootstrap "today" confirmation and a later poll's "tomorrow"
    # confirmation must coexist -- neither may silently overwrite the other.
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": _TODAY_ISO}], last_events=None)
    )
    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("saints_fc.every", AsyncMock()),
    ):
        await extension.start()  # confirms today

        extension._client = _mock_client(
            _router(next_events=[{"dateEventLocal": _TOMORROW_ISO}], last_events=None)
        )
        await extension._poll_once()  # confirms tomorrow, independently

    _today_theme = await extension.resolve_fallback(_FIXED_NOW)
    _tomorrow_theme = await extension.resolve_fallback(_FIXED_NOW + timedelta(days=1))
    assert _today_theme is not None
    assert _tomorrow_theme is not None
    await extension.stop()


async def test_start_schedules_an_interval_poll_at_the_default_cadence() -> None:
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=None, last_events=None))

    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("saints_fc.every", AsyncMock()) as _every,
    ):
        await extension.start()

    _every.assert_called_once_with(3600, extension._poll_once)
    await extension.stop()


async def test_start_schedules_an_interval_poll_at_a_configured_cadence() -> None:
    extension = SaintsFcExtension({"poll_interval_hours": 6})
    extension._client = _mock_client(_router(next_events=None, last_events=None))

    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("saints_fc.every", AsyncMock()) as _every,
    ):
        await extension.start()

    _every.assert_called_once_with(21600, extension._poll_once)
    await extension.stop()


async def test_stop_is_safe_before_start() -> None:
    # load_extensions() best-effort calls stop() on an extension whose
    # start() never ran (or was never reached) -- it must not raise on the
    # absent background task.
    extension = SaintsFcExtension({})

    await extension.stop()

    assert extension._task is None


async def test_stop_cancels_the_background_task_cleanly() -> None:
    extension = SaintsFcExtension({})
    extension._client = _mock_client(_router(next_events=None, last_events=None))

    with patch("saints_fc.datetime", _frozen_clock()):
        await extension.start()

    await extension.stop()

    assert extension._task is not None
    assert extension._task.cancelled() or extension._task.done()


@pytest.mark.parametrize("poll_interval_hours", [0, 0.0, -1, "6", None, True, ["6"]])
def test_init_rejects_a_non_positive_or_non_numeric_poll_interval(
    poll_interval_hours: object,
) -> None:
    # Scenario 10: poll_interval_hours must be a positive int/float. A bool,
    # string, None, list, zero or negative all raise at construction -- the
    # same ValueError style as api_key (describe the type, don't echo).
    with pytest.raises(ValueError):
        SaintsFcExtension({"poll_interval_hours": poll_interval_hours})


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
    extension = SaintsFcExtension({})
    extension._client = _mock_client(
        _router(next_events=[{"dateEventLocal": _TODAY_ISO}], last_events=None)
    )
    with (
        patch("saints_fc.datetime", _frozen_clock()),
        patch("saints_fc.every", AsyncMock()),
    ):
        await extension.start()  # bootstrap check confirms today

        _calls = {"n": 0}

        def _handler(request: httpx.Request) -> httpx.Response:
            _calls["n"] += 1
            # today's two endpoint calls succeed with no fixture; every call
            # after (tomorrow's check) fails.
            if _calls["n"] <= 2:
                _key = "events" if "eventsnext.php" in str(request.url) else "results"
                return httpx.Response(200, json={_key: None})
            return httpx.Response(500)

        extension._client = _mock_client(httpx.MockTransport(_handler))
        with (
            patch("common.decorator.asyncio.sleep", AsyncMock()),
            caplog.at_level(logging.WARNING),
        ):
            await extension._poll_once()  # tomorrow's check fails

    theme = await extension.resolve_fallback(_FIXED_NOW)
    assert theme is not None
    assert theme.effect_name == "saints_fc"
    # common.decorator.retry logs its own warning per retry attempt (already
    # covered by its own tests) -- what matters here is exactly one warning
    # from saints_fc itself, once retries for the failing date are exhausted.
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

    theme = await extension.resolve_fallback(_FIXED_NOW + timedelta(days=1))
    assert theme is not None
    # 5 calls: today's check fails its first eventsnext attempt, retries the
    # whole _fetch_kickoffs_on_date once (eventsnext + eventslast) and finds
    # no fixture for today; tomorrow's check is then two more calls and
    # succeeds first try.
    assert _calls["count"] == 5
    # The retry succeeded within the retry budget, so saints_fc's own
    # poll-failed warning (logged only once retries are exhausted) must not
    # fire -- only common.decorator.retry's own per-attempt warning does.
    assert not any("poll failed" in r.message for r in caplog.records)
