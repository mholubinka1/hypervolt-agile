import logging
from collections.abc import Sequence
from datetime import date, datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from hypervolt.charger import HypervoltChargerClient
from hypervolt.led import (
    THEMES_DIR,
    ExtensionWrapper,
    LedTheme,
    Window,
    load_custom_effect,
)
from octopus.client import AgileClient
from saints_fc import SaintsFcExtension
from schedule import Scheduler
from schedule.coordinator import ScheduleCoordinator

from config import AppConfig, Hypervolt, LedConfig, Octopus, Schedule

_UTC = ZoneInfo("UTC")
_SAINTS_LEDS = load_custom_effect(THEMES_DIR / "saints_fc.yaml")
_FIXTURE_DATE = date(2026, 8, 25)
_KICKOFF = datetime(2026, 8, 25, 15, 0, tzinfo=_UTC)
# Year-agnostic London-local window straddling the fixture date.
_NYE_WINDOW = ((8, 1, 0, 0), (9, 1, 0, 0))


def _config(led: LedConfig | None) -> AppConfig:
    return AppConfig(
        octopus=Octopus(account_number="A-1", api_key="key"),
        hypervolt=Hypervolt(username="user", password="pass"),
        schedule=Schedule(
            total_charge_duration=1,
            price_limit_incl_vat=10,
            update_every_mins=30,
            poll_every_secs=10,
        ),
        led=led,
    )


def _coordinator(
    led: LedConfig | None,
    is_charging: bool | None,
    car_plugged: bool | None = True,
) -> tuple[ScheduleCoordinator, HypervoltChargerClient]:
    charger_client = Mock(spec=HypervoltChargerClient)
    charger_client.apply_led_state = AsyncMock()
    charger_client.charger_state = Mock(
        is_charging=is_charging, car_plugged=car_plugged
    )

    coordinator = ScheduleCoordinator(scheduler=Mock(), config=_config(led))
    coordinator._charger_client = charger_client
    return coordinator, charger_client


def _saints_extension_on_a_fixture_date() -> ExtensionWrapper:
    # A real SaintsFcExtension with its fixture store seeded directly (its
    # httpx client is never exercised -- no poll happens in these tests).
    provider = SaintsFcExtension({})
    provider._matches = {_FIXTURE_DATE: [_KICKOFF]}
    return ExtensionWrapper(name="saints_fc", provider=provider)


def _real_resolve_coordinator(
    *,
    is_charging: bool | None,
    extensions: Sequence[ExtensionWrapper] = (),
    custom_themes: Sequence[tuple[LedTheme, Window, Window]] = (),
) -> tuple[ScheduleCoordinator, Mock]:
    # Like _coordinator() but leaves schedule.coordinator.resolve_theme real,
    # so the extension / custom-theme priority is exercised end to end.
    charger_client = Mock(spec=HypervoltChargerClient)
    charger_client.apply_led_state = AsyncMock()
    charger_client.charger_state = Mock(is_charging=is_charging, car_plugged=False)
    coordinator = ScheduleCoordinator(
        scheduler=Mock(),
        config=_config(LedConfig(enabled=True)),
        extensions=extensions,
        custom_themes=custom_themes,
    )
    coordinator._charger_client = charger_client
    return coordinator, charger_client


def _frozen_now(instant: datetime) -> object:
    # _apply_led_state reads datetime.now(ZoneInfo(TIMEZONE)); freezing it lets
    # a test place `now` before / inside / after the match window. Mirrors the
    # saints_fc tests' patch of saints_fc.datetime.
    _clock = Mock(wraps=datetime)
    _clock.now.return_value = instant
    return patch("schedule.coordinator.datetime", _clock)


async def test_activation_logs_the_theme_name_its_predicted_end_and_time_to_go(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 1: nothing lit -> the Saints strip lights the ring inside the
    # match window. KO 15:00 UTC (16:00 London BST) + 3h window end = 18:00 UTC
    # / 19:00 London; `now` frozen at 15:30 UTC leaves ~2h30m to go.
    coordinator, _ = _real_resolve_coordinator(
        is_charging=False, extensions=[_saints_extension_on_a_fixture_date()]
    )

    with (
        _frozen_now(datetime(2026, 8, 25, 15, 30, tzinfo=_UTC)),
        caplog.at_level(logging.INFO),
    ):
        await coordinator._apply_led_state()

    assert "LED theme 'saints_fc' active until 2026-08-25 19:00 (~2h30m)" in caplog.text


async def test_activation_line_omits_the_end_when_the_predicted_end_is_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 2: an always_on theme with no active_until -> the line names the
    # theme and stops there, with no ` until ` clause and no time-to-go.
    coordinator, _ = _coordinator(led=LedConfig(enabled=True), is_charging=False)

    with (
        patch(
            "schedule.coordinator.resolve_theme",
            return_value=LedTheme(effect_name="peace", always_on=True),
        ),
        caplog.at_level(logging.INFO),
    ):
        await coordinator._apply_led_state()

    _lines = [r.message for r in caplog.records if r.message.startswith("LED theme '")]
    assert _lines == ["LED theme 'peace' active"]


async def test_deactivation_logs_how_long_the_theme_was_lit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 3: a theme lights the ring on cycle 1, nothing resolves on cycle 2
    # -> a single line names the theme and the time it stayed lit (the gap
    # between the two frozen cycles).
    coordinator, _ = _coordinator(led=LedConfig(enabled=True), is_charging=True)

    with (
        patch(
            "schedule.coordinator.resolve_theme",
            side_effect=[LedTheme(effect_name="peace", always_on=True), None],
        ),
        caplog.at_level(logging.INFO),
    ):
        with _frozen_now(datetime(2026, 8, 25, 15, 0, tzinfo=_UTC)):
            await coordinator._apply_led_state()
        with _frozen_now(datetime(2026, 8, 25, 15, 47, tzinfo=_UTC)):
            await coordinator._apply_led_state()

    _lines = [r.message for r in caplog.records if r.message.startswith("LED theme '")]
    assert _lines == [
        "LED theme 'peace' active",
        "LED theme 'peace' cleared after 47m",
    ]


async def test_a_swap_is_recorded_as_one_line_naming_both_themes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 4: theme A lights the ring on cycle 1, a different theme B takes
    # over on cycle 2 -> exactly one new line, naming B (with its predicted end)
    # and the theme it replaced with how long that ran. No separate cleared line.
    coordinator, _ = _coordinator(led=LedConfig(enabled=True), is_charging=True)

    with (
        patch(
            "schedule.coordinator.resolve_theme",
            side_effect=[
                LedTheme(effect_name="valentines", always_on=True),
                LedTheme(
                    effect_name="bonfire",
                    always_on=True,
                    active_until=datetime(2026, 8, 25, 19, 0, tzinfo=_UTC),
                ),
            ],
        ),
        caplog.at_level(logging.INFO),
    ):
        with _frozen_now(datetime(2026, 8, 25, 15, 0, tzinfo=_UTC)):
            await coordinator._apply_led_state()
        with _frozen_now(datetime(2026, 8, 25, 15, 47, tzinfo=_UTC)):
            await coordinator._apply_led_state()

    _lines = [r.message for r in caplog.records if r.message.startswith("LED theme '")]
    assert _lines == [
        "LED theme 'valentines' active",
        (
            "LED theme 'bonfire' active until 2026-08-25 20:00 (~3h13m) "
            "— replaced 'valentines' after 47m"
        ),
    ]
    assert not any("cleared" in message for message in _lines)


async def test_a_steady_theme_logs_one_transition_line_across_many_cycles(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 5: the same theme resolves and displays on cycle after cycle
    # (clock advancing) -> exactly one activation line, nothing on the repeats.
    coordinator, _ = _coordinator(led=LedConfig(enabled=True), is_charging=True)

    with (
        patch(
            "schedule.coordinator.resolve_theme",
            return_value=LedTheme(effect_name="saints_fc", always_on=True),
        ),
        caplog.at_level(logging.INFO),
    ):
        for _minute in (0, 15, 30, 45):
            with _frozen_now(datetime(2026, 8, 25, 15, _minute, tzinfo=_UTC)):
                await coordinator._apply_led_state()

    _transitions = [
        r.message
        for r in caplog.records
        if r.message.startswith("LED theme '")
        and (" active" in r.message or " cleared" in r.message)
    ]
    assert _transitions == ["LED theme 'saints_fc' active"]


async def test_a_fresh_coordinator_logs_an_activation_on_its_first_display(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 6: a brand-new coordinator (post-restart, nothing tracked yet)
    # whose very first LED cycle displays a theme logs a plain activation, not
    # a "replaced ..." line -- the exact-match assertion below would fail on
    # the latter.
    coordinator, _ = _coordinator(led=LedConfig(enabled=True), is_charging=True)

    with (
        patch(
            "schedule.coordinator.resolve_theme",
            return_value=LedTheme(effect_name="peace", always_on=True),
        ),
        caplog.at_level(logging.INFO),
    ):
        await coordinator._apply_led_state()

    _lines = [r.message for r in caplog.records if r.message.startswith("LED theme '")]
    assert _lines == ["LED theme 'peace' active"]


async def test_a_dark_ring_that_stays_dark_logs_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Transition table, none -> none: nothing is displayed and nothing was
    # displayed, so no line is logged and the tracker stays empty.
    coordinator, _ = _coordinator(led=LedConfig(enabled=True), is_charging=True)

    with (
        patch("schedule.coordinator.resolve_theme", return_value=None),
        caplog.at_level(logging.INFO),
    ):
        await coordinator._apply_led_state()

    assert not any(r.message.startswith("LED theme '") for r in caplog.records)
    assert coordinator._active_theme_name is None


async def test_a_cleared_theme_with_no_recorded_start_reports_a_zero_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Defensive: if a theme name is tracked without a start instant (should not
    # happen), clearing it still logs a well-formed line rather than crashing
    # the run loop.
    coordinator, _ = _coordinator(led=LedConfig(enabled=True), is_charging=True)
    coordinator._active_theme_name = "saints_fc"
    coordinator._active_theme_since = None

    with (
        patch("schedule.coordinator.resolve_theme", return_value=None),
        caplog.at_level(logging.INFO),
    ):
        await coordinator._apply_led_state()

    _lines = [r.message for r in caplog.records if r.message.startswith("LED theme '")]
    assert _lines == ["LED theme 'saints_fc' cleared after 0s"]


async def test_a_naive_active_until_is_logged_without_a_predicted_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A third-party extension could hand back a naive active_until, which can't
    # be compared to the aware `now`. The activation line still logs -- just
    # without the ` until ...` clause -- rather than raising mid-cycle.
    _naive_end = datetime(2026, 8, 25, 19, 0)  # noqa: DTZ001 -- naive on purpose
    coordinator, _ = _coordinator(led=LedConfig(enabled=True), is_charging=True)

    with (
        patch(
            "schedule.coordinator.resolve_theme",
            return_value=LedTheme(
                effect_name="rogue", always_on=True, active_until=_naive_end
            ),
        ),
        caplog.at_level(logging.INFO),
    ):
        await coordinator._apply_led_state()

    _lines = [r.message for r in caplog.records if r.message.startswith("LED theme '")]
    assert _lines == ["LED theme 'rogue' active"]


async def test_a_failed_ring_push_is_not_recorded_as_an_active_theme(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The log tracks what reached the ring, not what merely resolved: if the
    # wire push raises, no transition line is logged and the tracker does not
    # advance, so the next cycle retries and logs it.
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=True
    )
    charger_client.apply_led_state.side_effect = RuntimeError("websocket down")

    with (
        patch(
            "schedule.coordinator.resolve_theme",
            return_value=LedTheme(effect_name="peace", always_on=True),
        ),
        caplog.at_level(logging.INFO),
        pytest.raises(RuntimeError),
    ):
        await coordinator._apply_led_state()

    assert not any(r.message.startswith("LED theme '") for r in caplog.records)
    assert coordinator._active_theme_name is None
    assert coordinator._active_theme_since is None


@pytest.mark.parametrize(
    "led, is_charging",
    [
        (LedConfig(enabled=True), None),  # 7(a): charge state unknown
        (None, True),  # 7(b): no led block
        (LedConfig(enabled=False), True),  # 7(b): led disabled
    ],
)
async def test_an_early_return_cycle_leaves_the_tracked_theme_untouched(
    led: LedConfig | None,
    is_charging: bool | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scenario 7: when _apply_led_state bails before resolving a theme, whatever
    # was on the ring is still on it -- the tracker must not move.
    coordinator, _ = _coordinator(led=led, is_charging=is_charging)
    _since = datetime(2026, 8, 25, 15, 0, tzinfo=_UTC)
    coordinator._active_theme_name = "saints_fc"
    coordinator._active_theme_since = _since

    with caplog.at_level(logging.INFO):
        await coordinator._apply_led_state()

    assert not any(r.message.startswith("LED theme '") for r in caplog.records)
    assert coordinator._active_theme_name == "saints_fc"
    assert coordinator._active_theme_since == _since


async def test_saints_window_outranks_a_custom_theme_only_during_the_match() -> None:
    # End-to-end (scenario 10): on a fixture date with an always_on custom
    # theme configured, the custom theme shows either side of the match window
    # and the Saints strip takes over inside it -- all while not charging.
    _nye = LedTheme(
        effect_name="nye", leds=[{"r": 0.1, "g": 0.2, "b": 0.3}], always_on=True
    )
    coordinator, charger_client = _real_resolve_coordinator(
        is_charging=False,
        extensions=[_saints_extension_on_a_fixture_date()],
        custom_themes=[(_nye, *_NYE_WINDOW)],
    )

    with _frozen_now(datetime(2026, 8, 25, 14, 0, tzinfo=_UTC)):  # before KO-30m
        await coordinator._apply_led_state()
    charger_client.apply_led_state.assert_awaited_once_with(1.0, "nye", leds=_nye.leds)

    charger_client.apply_led_state.reset_mock()
    with _frozen_now(datetime(2026, 8, 25, 15, 30, tzinfo=_UTC)):  # inside the window
        await coordinator._apply_led_state()
    charger_client.apply_led_state.assert_awaited_once_with(
        1.0, "saints_fc", leds=_SAINTS_LEDS
    )

    charger_client.apply_led_state.reset_mock()
    with _frozen_now(datetime(2026, 8, 25, 18, 30, tzinfo=_UTC)):  # after KO+3h
        await coordinator._apply_led_state()
    charger_client.apply_led_state.assert_awaited_once_with(1.0, "nye", leds=_nye.leds)


async def test_bare_charger_shows_the_saints_fallback_only_while_charging() -> None:
    # End-to-end (scenario 10): no custom theme, `now` outside the window. The
    # Saints fallback is charging-gated, so the charger is off when idle and
    # shows the strip once charging.
    _idle, _idle_client = _real_resolve_coordinator(
        is_charging=False, extensions=[_saints_extension_on_a_fixture_date()]
    )
    with _frozen_now(datetime(2026, 8, 25, 9, 0, tzinfo=_UTC)):
        await _idle._apply_led_state()
    _idle_client.apply_led_state.assert_awaited_once_with(0.0, None)

    _charging, _charging_client = _real_resolve_coordinator(
        is_charging=True, extensions=[_saints_extension_on_a_fixture_date()]
    )
    with _frozen_now(datetime(2026, 8, 25, 9, 0, tzinfo=_UTC)):
        await _charging._apply_led_state()
    _charging_client.apply_led_state.assert_awaited_once_with(
        1.0, "saints_fc", leds=_SAINTS_LEDS
    )


async def test_pushes_off_state_when_no_theme_resolves_and_charging() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=True
    )

    with patch("schedule.coordinator.resolve_theme", return_value=None):
        await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(0.0, None)


async def test_pushes_off_state_when_no_theme_resolves_and_not_charging() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=False
    )

    await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(0.0, None)


async def test_pushes_off_state_when_charging_gated_theme_resolves_but_not_charging() -> (
    None
):
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=False
    )

    with patch(
        "schedule.coordinator.resolve_theme",
        return_value=LedTheme(effect_name="halloween_mode", always_on=False),
    ):
        await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(0.0, None)


async def test_makes_no_charger_call_when_charging_state_is_unknown() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=None
    )

    with patch(
        "schedule.coordinator.resolve_theme",
        return_value=LedTheme(effect_name="halloween_mode", always_on=True),
    ):
        await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_not_awaited()


async def test_passes_custom_themes_through_to_resolve_theme() -> None:
    custom_themes = [(LedTheme(effect_name="peace"), (3, 14, 0, 0), (3, 16, 0, 0))]
    coordinator, _charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=True
    )
    coordinator._custom_themes = custom_themes

    with patch(
        "schedule.coordinator.resolve_theme", return_value=None
    ) as mock_resolve_theme:
        await coordinator._apply_led_state()

    _, kwargs = mock_resolve_theme.call_args
    assert kwargs["custom_themes"] == custom_themes


async def test_passes_built_in_themes_through_to_resolve_theme() -> None:
    built_in_themes = [
        (LedTheme(effect_name="christmas_mode"), (12, 24, 0, 0), (12, 31, 6, 0))
    ]
    coordinator, _charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=True
    )
    coordinator._built_in_themes = built_in_themes

    with patch(
        "schedule.coordinator.resolve_theme", return_value=None
    ) as mock_resolve_theme:
        await coordinator._apply_led_state()

    _, kwargs = mock_resolve_theme.call_args
    assert kwargs["built_in_themes"] == built_in_themes


async def test_passes_extensions_through_to_resolve_theme() -> None:
    extensions = [ExtensionWrapper(name="saints_fc", provider=Mock())]
    charger_client = Mock(spec=HypervoltChargerClient)
    charger_client.apply_led_state = AsyncMock()
    charger_client.charger_state = Mock(is_charging=True)
    coordinator = ScheduleCoordinator(
        scheduler=Mock(),
        config=_config(LedConfig(enabled=True)),
        extensions=extensions,
    )
    coordinator._charger_client = charger_client

    with patch(
        "schedule.coordinator.resolve_theme", return_value=None
    ) as mock_resolve_theme:
        await coordinator._apply_led_state()

    _, kwargs = mock_resolve_theme.call_args
    assert kwargs["extensions"] == extensions


async def test_pushes_custom_theme_leds_at_full_brightness_while_charging() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=True
    )
    leds = [{"r": 0.0, "g": 0.34, "b": 0.72}]

    with patch(
        "schedule.coordinator.resolve_theme",
        return_value=LedTheme(effect_name="peace", leds=leds, always_on=False),
    ):
        await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(1.0, "peace", leds=leds)


async def test_pushes_resolved_theme_effect_at_full_brightness_while_charging() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=True
    )

    with patch(
        "schedule.coordinator.resolve_theme",
        return_value=LedTheme(effect_name="halloween_mode"),
    ):
        await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(
        1.0, "halloween_mode", leds=None
    )


async def test_plug_state_does_not_gate_a_charging_gated_theme_while_charging() -> None:
    # car_plugged is no longer consulted for LED display (ADR 0014 supersedes
    # ADR 0010): a charging-gated theme shows while charging regardless of it.
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=True, car_plugged=False
    )

    with patch(
        "schedule.coordinator.resolve_theme",
        return_value=LedTheme(effect_name="halloween_mode", always_on=False),
    ):
        await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(
        1.0, "halloween_mode", leds=None
    )


async def test_sends_no_led_messages_when_no_led_block_in_config() -> None:
    coordinator, charger_client = _coordinator(led=None, is_charging=True)

    await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_not_awaited()


async def test_sends_no_led_messages_when_led_disabled() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=False), is_charging=True
    )

    await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_not_awaited()


async def test_always_on_theme_lights_the_charger_at_full_brightness_when_not_charging() -> (
    None
):
    # Tracer bullet for issue #114: an always_on theme is displayed for its whole
    # window regardless of charge state, at full brightness -- and plug state is
    # not consulted at all.
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=False, car_plugged=False
    )
    leds = [{"r": 0.84, "g": 0.1, "b": 0.13}]

    with patch(
        "schedule.coordinator.resolve_theme",
        return_value=LedTheme(effect_name="saints_fc", leds=leds, always_on=True),
    ):
        await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(1.0, "saints_fc", leds=leds)


async def test_run_applies_led_state_even_when_schedule_cannot_be_pushed() -> None:
    # car_plugged=False makes _can_push() False, so schedule/lock control are
    # skipped this cycle -- LED state must still be applied regardless.
    charger_client = Mock(spec=HypervoltChargerClient)
    charger_client.apply_led_state = AsyncMock()
    charger_client.refresh = AsyncMock()
    charger_client.is_connected = True
    charger_client.charger_state = Mock(
        car_plugged=False,
        release_state=None,
        is_charging=True,
    )

    agile_client = Mock(spec=AgileClient)
    agile_client.get_upcoming_prices = AsyncMock(return_value=[])
    scheduler = Scheduler(
        agile_client=agile_client, config=_config(LedConfig(enabled=True))
    )

    coordinator = ScheduleCoordinator(
        scheduler=scheduler, config=_config(LedConfig(enabled=True))
    )
    coordinator._charger_client = charger_client

    with patch("schedule.coordinator.resolve_theme", return_value=None):
        await coordinator.run()

    charger_client.apply_led_state.assert_awaited_once_with(0.0, None)
