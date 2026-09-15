from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

from common.extensions import ExtensionWrapper
from common.model import Price
from octopus.client import AgileClient
from schedule import Scheduler

from config import AppConfig, Hypervolt, Octopus, Schedule

_UTC = ZoneInfo("UTC")


def _config(price_limit_incl_vat: float) -> AppConfig:
    return AppConfig(
        octopus=Octopus(account_number="A-1", api_key="key"),
        hypervolt=Hypervolt(username="user", password="pass"),
        schedule=Schedule(
            total_charge_duration=1,
            price_limit_incl_vat=price_limit_incl_vat,
            update_every_mins=30,
            poll_every_secs=10,
        ),
    )


def _half_hour_price(value_exc_vat: float, slot_index: int, start: datetime) -> Price:
    _start = start + timedelta(minutes=30 * slot_index)
    return Price(
        value_exc_vat=value_exc_vat,
        valid_from=_start,
        valid_to=_start + timedelta(minutes=30),
    )


def _agile_client(prices: list[Price]) -> Mock:
    _client = Mock(spec=AgileClient)
    _client.get_upcoming_prices = AsyncMock(return_value=prices)
    return _client


class _FixedThresholdProvider:
    def __init__(self, config: dict, value: float = 5.0) -> None:
        self.config = config
        self._value = value

    async def get_threshold(self) -> float | None:
        return self._value


class _NoFreshValueThresholdProvider:
    def __init__(self, config: dict) -> None:
        self.config = config

    async def get_threshold(self) -> float | None:
        return None


class _ChangingThresholdProvider:
    def __init__(self, values: list[float]) -> None:
        self._values = iter(values)

    async def get_threshold(self) -> float | None:
        return next(self._values)


async def test_scheduler_uses_the_threshold_providers_fresh_value_instead_of_the_static_config() -> (
    None
):
    # Static price_limit_incl_vat is 100p -- if the dynamic provider's 5p
    # value weren't taking effect, every period below would still be under
    # the static limit and a session would still build.
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(50, 0, _now)]  # 50p exc VAT -- above a 5p limit
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_FixedThresholdProvider({}),
        kind="charging threshold",
    )
    scheduler = Scheduler(
        _agile_client(prices),
        _config(price_limit_incl_vat=100),
        threshold_provider=threshold_provider,
    )

    scheduler.invalidate()
    await scheduler.update()

    assert scheduler.schedule == []


async def test_scheduler_falls_back_to_the_static_limit_when_the_threshold_provider_has_no_fresh_value() -> (
    None
):
    # Static price_limit_incl_vat is 100p -- a 50p price should still clear
    # it and build a session, proving a None from the provider means "use
    # the static config for this cycle", not "treat as a zero/blocking limit".
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(50, 0, _now)]  # 50p exc VAT -- under a 100p limit
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_NoFreshValueThresholdProvider({}),
        kind="charging threshold",
    )
    scheduler = Scheduler(
        _agile_client(prices),
        _config(price_limit_incl_vat=100),
        threshold_provider=threshold_provider,
    )

    scheduler.invalidate()
    await scheduler.update()

    assert len(scheduler.schedule) == 1


async def test_scheduler_uses_the_static_limit_when_no_threshold_provider_is_configured() -> (
    None
):
    # No threshold_provider passed at all (the default) -- proves this
    # feature is genuinely opt-in and doesn't change today's behaviour.
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(50, 0, _now)]  # 50p exc VAT -- under a 100p limit
    scheduler = Scheduler(
        _agile_client(prices),
        _config(price_limit_incl_vat=100),
    )

    scheduler.invalidate()
    await scheduler.update()

    assert len(scheduler.schedule) == 1


async def test_scheduler_refreshes_the_threshold_on_the_next_new_prices_rebuild_not_only_replug() -> (
    None
):
    # _rebuild_on_new_prices() is a second, independent call site to the
    # same update_limit()-before-build() wiring the replug-triggered tests
    # above already cover -- a regression that only wired the replug path
    # would pass every other test in this file silently.
    _now = datetime.now(tz=_UTC)
    _later = _now + timedelta(hours=2)
    _client = Mock(spec=AgileClient)
    _client.get_upcoming_prices = AsyncMock(
        side_effect=[
            [_half_hour_price(50, 0, _now)],
            [_half_hour_price(50, 0, _later)],
        ]
    )
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_ChangingThresholdProvider([5.0, 60.0]),
        kind="charging threshold",
    )
    scheduler = Scheduler(
        _client,
        _config(price_limit_incl_vat=100),
        threshold_provider=threshold_provider,
    )

    scheduler.invalidate()
    await scheduler.update()
    assert scheduler.schedule == []  # 5p limit blocks the 50p price

    await scheduler._rebuild_on_new_prices()

    assert len(scheduler.schedule) == 1  # 60p limit now admits the 50p price


async def test_scheduler_correctly_converts_the_dynamic_thresholds_incl_vat_pence_to_exc_vat() -> (
    None
):
    # 21p incl VAT converts to exactly 20p exc VAT (21 / 1.05). A 20.5p
    # exc-VAT price sits between the two: correctly converted, it's above
    # the limit (no session); if the /ELECTRICITY_VAT_RATE conversion were
    # skipped (comparing against the raw 21p instead), it would incorrectly
    # qualify and build a session.
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(20.5, 0, _now)]
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_FixedThresholdProvider({}, value=21.0),
        kind="charging threshold",
    )
    scheduler = Scheduler(
        _agile_client(prices),
        _config(price_limit_incl_vat=100),
        threshold_provider=threshold_provider,
    )

    scheduler.invalidate()
    await scheduler.update()

    assert scheduler.schedule == []
