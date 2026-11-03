import logging
from unittest.mock import AsyncMock

import pytest
from hypervolt.client.websocket import HypervoltWebSocketClient
from hypervolt.model import ActivationMode, HypervoltCharger
from hypervolt.state import HypervoltChargerState


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


_ULTRA_SCHEDULES_GET = {
    "applied": {
        "sessions": [
            {
                "session_type": "unique",
                "start_time": "2026-11-02T23:00:00Z",
                "end_time": "2026-11-03T01:00:00Z",
                "mode": "boost",
                "max_power_watt": 7400,
                "max_energy_watt_hours": 14800,
            },
            {
                "session_type": "unique",
                "start_time": "2026-11-03T01:30:00Z",
                "end_time": "2026-11-03T02:30:00Z",
                "mode": "boost",
                "max_power_watt": 7400,
                "max_energy_watt_hours": 7400,
            },
        ],
        "enabled": True,
        "is_default": False,
        "type": "axle",
    },
    "pending": {},
}


async def test_an_ultra_schedule_is_reported_as_ultra_mode_without_a_parse_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client()

    with caplog.at_level(logging.WARNING):
        await client._protocol.handle("schedules.get", _ULTRA_SCHEDULES_GET, None)

    delta = client._protocol._on_state_update.call_args.args[0]
    assert delta.activation_mode == ActivationMode.ultra
    assert delta.current_schedule == []
    assert "parse error" not in caplog.text


_RECURRING_SESSION = {
    "session_type": "recurring",
    "start_time": "23:00:00",
    "end_time": "05:00:00",
    "days": [0, 1, 2, 3, 4, 5, 6],
    "mode": "boost",
    "max_power_watt": 7400,
}


def _schedules_get(type_: str, sessions: list[dict]) -> dict:
    return {
        "applied": {"sessions": sessions, "enabled": True, "type": type_},
        "pending": {},
    }


async def test_an_ultra_schedule_with_no_sessions_is_still_ultra_mode() -> None:
    client = _client()

    await client._protocol.handle("schedules.get", _schedules_get("axle", []), None)

    delta = client._protocol._on_state_update.call_args.args[0]
    assert delta.activation_mode == ActivationMode.ultra


async def test_entering_ultra_logs_one_info_across_repeated_polls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client()

    with caplog.at_level(logging.INFO):
        for _ in range(3):
            await client._protocol.handle("schedules.get", _ULTRA_SCHEDULES_GET, None)

    ultra_infos = [r for r in caplog.records if "Ultra" in r.getMessage()]
    assert len(ultra_infos) == 1
    assert ultra_infos[0].levelno == logging.INFO


async def test_leaving_ultra_logs_one_info_and_restores_the_normal_mode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client()
    await client._protocol.handle("schedules.get", _ULTRA_SCHEDULES_GET, None)

    with caplog.at_level(logging.INFO):
        for _ in range(2):
            await client._protocol.handle(
                "schedules.get",
                _schedules_get("recurring", [_RECURRING_SESSION]),
                None,
            )

    delta = client._protocol._on_state_update.call_args.args[0]
    assert delta.activation_mode == ActivationMode.schedule
    leaving = [r for r in caplog.records if "no longer" in r.getMessage()]
    assert len(leaving) == 1
    assert leaving[0].levelno == logging.INFO


async def test_a_malformed_recurring_schedule_still_logs_the_parse_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client()

    with caplog.at_level(logging.WARNING):
        await client._protocol.handle(
            "schedules.get",
            _schedules_get("recurring", [{"session_type": "recurring"}]),
            None,
        )

    assert "parse error" in caplog.text


async def test_ultra_ending_with_no_schedule_restores_a_normal_activation_mode() -> (
    None
):
    client = _client()
    state = HypervoltChargerState(HypervoltCharger(id="charger-1", maj_version=3))
    client._protocol._on_state_update = AsyncMock(side_effect=state.update)

    await client._protocol.handle("schedules.get", _ULTRA_SCHEDULES_GET, None)
    assert state.activation_mode == ActivationMode.ultra

    await client._protocol.handle("schedules.get", {"applied": None}, None)

    assert state.activation_mode == ActivationMode.plug_and_charge


async def test_ultra_ending_with_no_schedule_is_logged_and_a_return_is_logged_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _client()
    protocol = client._protocol

    with caplog.at_level(logging.INFO):
        await protocol.handle("schedules.get", _schedules_get("axle", []), None)
        await protocol.handle("schedules.get", {"applied": None}, None)
        await protocol.handle("schedules.get", _schedules_get("axle", []), None)

    assert caplog.text.count("Ultra schedule detected") == 2
    assert caplog.text.count("Ultra schedule no longer applied") == 1
