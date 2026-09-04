from datetime import date, datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from hypervolt.charger import HypervoltChargerClient
from hypervolt.led import THEMES_DIR, ExtensionWrapper, LedTheme, load_custom_effect
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
    extensions: object = (),
    custom_themes: object = (),
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
