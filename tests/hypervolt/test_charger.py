from datetime import time
from unittest.mock import AsyncMock, Mock

from hypervolt.charger import HypervoltChargerClient
from hypervolt.model import DayOfWeek, HypervoltCharger, HypervoltSession


def _charger_client(
    led_brightness: float | None = None,
    current_led_effect: str | None = None,
    current_leds: list[dict[str, float]] | None = None,
    current_schedule: list[HypervoltSession] | None = None,
) -> tuple[HypervoltChargerClient, Mock]:
    charger = HypervoltCharger(id="charger-1", maj_version=3)
    rest_client = Mock(charger=charger)
    client = HypervoltChargerClient(rest_client=rest_client, polling_interval=10)
    client._charger_state.led_brightness = led_brightness
    client._current_led_effect = current_led_effect
    client._current_leds = current_leds
    client._charger_state.current_schedule = current_schedule

    ws_client = Mock()
    ws_client.is_connected = True
    ws_client.set_led_brightness = AsyncMock()
    ws_client.set_led_effect = AsyncMock()
    ws_client.set_charging_schedule = AsyncMock()
    client._ws_client = ws_client

    return client, ws_client


async def test_apply_led_state_pushes_brightness_when_it_differs() -> None:
    client, ws_client = _charger_client(led_brightness=0.0)

    await client.apply_led_state(0.5, None)

    ws_client.set_led_brightness.assert_awaited_once_with(0.5)


async def test_apply_led_state_skips_push_when_brightness_already_matches() -> None:
    client, ws_client = _charger_client(led_brightness=0.5)

    await client.apply_led_state(0.5, None)

    ws_client.set_led_brightness.assert_not_awaited()


async def test_apply_led_state_pushes_effect_when_it_differs() -> None:
    client, ws_client = _charger_client(led_brightness=0.5, current_led_effect=None)

    await client.apply_led_state(0.5, "halloween_mode")

    ws_client.set_led_effect.assert_awaited_once_with("halloween_mode")
    assert client._current_led_effect == "halloween_mode"


async def test_apply_led_state_skips_push_when_effect_already_matches() -> None:
    client, ws_client = _charger_client(
        led_brightness=0.5, current_led_effect="halloween_mode"
    )

    await client.apply_led_state(0.5, "halloween_mode")

    ws_client.set_led_effect.assert_not_awaited()


async def test_apply_led_state_sends_none_sentinel_when_theme_ends() -> None:
    # "none" is the wire sentinel the charger understands as "stop showing an
    # effect" -- without sending it explicitly, a theme would keep showing on
    # the physical charger indefinitely once its window closed.
    client, ws_client = _charger_client(
        led_brightness=0.5, current_led_effect="halloween_mode"
    )

    await client.apply_led_state(0.5, None)

    ws_client.set_led_effect.assert_awaited_once_with("none")
    assert client._current_led_effect is None


async def test_apply_led_state_sends_no_redundant_none_when_nothing_was_active() -> (
    None
):
    client, ws_client = _charger_client(led_brightness=0.5, current_led_effect=None)

    await client.apply_led_state(0.5, None)

    ws_client.set_led_effect.assert_not_awaited()


async def test_apply_led_state_pushes_steady_array_with_leds_for_a_custom_theme() -> (
    None
):
    client, ws_client = _charger_client(led_brightness=0.5, current_led_effect=None)
    leds = [{"r": 0.0, "g": 0.34, "b": 0.72}]

    await client.apply_led_state(0.5, "peace", leds=leds)

    ws_client.set_led_effect.assert_awaited_once_with("steady_array", leds=leds)
    assert client._current_led_effect == "peace"


async def test_apply_led_state_detects_switch_between_two_custom_themes() -> None:
    # Both wire as "steady_array" -- only effect_name (the semantic identity)
    # distinguishes them, so this must not be treated as "unchanged".
    client, ws_client = _charger_client(led_brightness=0.5, current_led_effect="peace")
    new_leds = [{"r": 1.0, "g": 1.0, "b": 1.0}]

    await client.apply_led_state(0.5, "st_george", leds=new_leds)

    ws_client.set_led_effect.assert_awaited_once_with("steady_array", leds=new_leds)
    assert client._current_led_effect == "st_george"


async def test_apply_led_state_skips_redundant_push_for_same_custom_theme() -> None:
    client, ws_client = _charger_client(
        led_brightness=0.5,
        current_led_effect="peace",
        current_leds=[{"r": 0.0, "g": 0.34, "b": 0.72}],
    )

    await client.apply_led_state(0.5, "peace", leds=[{"r": 0.0, "g": 0.34, "b": 0.72}])

    ws_client.set_led_effect.assert_not_awaited()


async def test_apply_led_state_pushes_when_leds_differ_despite_unchanged_effect_name() -> (
    None
):
    # effect_name is chosen by config/extension authors and isn't guaranteed
    # unique across sources (only built-in names are reserved) -- a custom
    # theme and an extension could legally share a name. Diffing on
    # effect_name alone would then silently skip a real leds change whenever
    # the name happens to be unchanged.
    client, ws_client = _charger_client(
        led_brightness=0.5,
        current_led_effect="saints_fc_matchday",
        current_leds=[{"r": 0.0, "g": 0.0, "b": 0.0}],
    )
    new_leds = [{"r": 1.0, "g": 0.0, "b": 0.0}]

    await client.apply_led_state(0.5, "saints_fc_matchday", leds=new_leds)

    ws_client.set_led_effect.assert_awaited_once_with("steady_array", leds=new_leds)


async def test_apply_schedule_treats_a_reordered_but_equal_schedule_as_unchanged() -> (
    None
):
    # Comparing sessions requires sorting them into a stable order, and that
    # order is keyed in part by day_of_week -- proposed and stored schedules
    # arrive in whatever order their source produced, not necessarily the
    # same one, so ordering by day must not depend on member declaration
    # matching call-site order.
    monday_session = HypervoltSession(
        start=time(8, 0), end=time(16, 0), day_of_week=DayOfWeek.monday
    )
    friday_session = HypervoltSession(
        start=time(8, 0), end=time(16, 0), day_of_week=DayOfWeek.friday
    )
    client, ws_client = _charger_client(
        current_schedule=[friday_session, monday_session]
    )

    pushed_again = await client.apply_schedule([monday_session, friday_session])

    assert pushed_again is False
    ws_client.set_charging_schedule.assert_not_awaited()


async def test_apply_schedule_pushes_when_a_session_day_of_week_actually_changes() -> (
    None
):
    monday_session = HypervoltSession(
        start=time(8, 0), end=time(16, 0), day_of_week=DayOfWeek.monday
    )
    tuesday_session = HypervoltSession(
        start=time(8, 0), end=time(16, 0), day_of_week=DayOfWeek.tuesday
    )
    client, ws_client = _charger_client(current_schedule=[monday_session])

    pushed_again = await client.apply_schedule([tuesday_session])

    assert pushed_again is True
    ws_client.set_charging_schedule.assert_awaited_once()
