from unittest.mock import AsyncMock

from hypervolt.client.websocket import HypervoltWebSocketClient
from hypervolt.model import HypervoltCharger


def _client() -> HypervoltWebSocketClient:
    charger = HypervoltCharger(id="charger-1", maj_version=3)
    client = HypervoltWebSocketClient(
        charger=charger,
        access_token_callback=AsyncMock(),
        on_state_update=AsyncMock(),
    )
    client._send_message = AsyncMock()  # type: ignore[method-assign]
    return client


async def test_set_led_effect_serializes_effect_name_only_when_no_leds() -> None:
    client = _client()

    await client.set_led_effect("halloween_mode")

    sent = client._send_message.call_args.args[0]
    assert sent["method"] == "sync.apply"
    assert sent["params"] == {"effect_name": "halloween_mode"}


async def test_set_led_effect_serializes_leds_array_when_provided() -> None:
    client = _client()
    leds = [{"r": 0.0, "g": 0.34, "b": 0.72}]

    await client.set_led_effect("steady_array", leds=leds)

    sent = client._send_message.call_args.args[0]
    assert sent["params"] == {"effect_name": "steady_array", "leds": leds}
