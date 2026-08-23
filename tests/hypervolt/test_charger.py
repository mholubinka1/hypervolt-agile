from unittest.mock import AsyncMock, Mock

from hypervolt.charger import HypervoltChargerClient
from hypervolt.model import HypervoltCharger


def _charger_client(
    led_brightness: float | None,
) -> tuple[HypervoltChargerClient, Mock]:
    charger = HypervoltCharger(id="charger-1", maj_version=3)
    rest_client = Mock(charger=charger)
    client = HypervoltChargerClient(rest_client=rest_client, polling_interval=10)
    client._charger_state.led_brightness = led_brightness

    ws_client = Mock()
    ws_client.is_connected = True
    ws_client.set_led_brightness = AsyncMock()
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
