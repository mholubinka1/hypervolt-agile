from unittest.mock import AsyncMock, Mock

from hypervolt.charger import HypervoltChargerClient
from schedule.coordinator import ScheduleCoordinator

from config import AppConfig, Hypervolt, LedConfig, Octopus, Schedule


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
    led: LedConfig | None, is_charging: bool | None, led_brightness: float | None
) -> tuple[ScheduleCoordinator, HypervoltChargerClient]:
    charger_client = Mock(spec=HypervoltChargerClient)
    charger_client.apply_led_state = AsyncMock()
    charger_client.charger_state = Mock(
        is_charging=is_charging, led_brightness=led_brightness
    )

    coordinator = ScheduleCoordinator(scheduler=Mock(), config=_config(led))
    coordinator._charger_client = charger_client
    return coordinator, charger_client


async def test_pushes_half_brightness_when_charging_starts() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=True, led_brightness=0.0
    )

    await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(0.5, None)


async def test_pushes_zero_brightness_when_charging_stops() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True), is_charging=False, led_brightness=1.0
    )

    await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(0.0, None)


async def test_pushes_configured_brightness_instead_of_default() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True, brightness=0.8),
        is_charging=True,
        led_brightness=0.0,
    )

    await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(0.8, None)


async def test_off_state_ignores_configured_brightness() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=True, brightness=0.8),
        is_charging=False,
        led_brightness=1.0,
    )

    await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_awaited_once_with(0.0, None)


async def test_sends_no_led_messages_when_no_led_block_in_config() -> None:
    coordinator, charger_client = _coordinator(
        led=None, is_charging=True, led_brightness=0.0
    )

    await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_not_awaited()


async def test_sends_no_led_messages_when_led_disabled() -> None:
    coordinator, charger_client = _coordinator(
        led=LedConfig(enabled=False), is_charging=True, led_brightness=0.0
    )

    await coordinator._apply_led_state()

    charger_client.apply_led_state.assert_not_awaited()


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
        led_brightness=0.0,
    )

    scheduler = Mock()
    scheduler.update = AsyncMock()
    scheduler.should_verify = Mock(return_value=False)

    coordinator = ScheduleCoordinator(
        scheduler=scheduler, config=_config(LedConfig(enabled=True))
    )
    coordinator._charger_client = charger_client

    await coordinator.run()

    charger_client.apply_led_state.assert_awaited_once_with(0.5, None)
