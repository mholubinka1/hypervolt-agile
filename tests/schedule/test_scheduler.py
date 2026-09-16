import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from common.extensions import ExtensionWrapper
from common.model import Price
from octopus.client import AgileClient
from schedule import Scheduler

from config import (
    AppConfig,
    ExtensionEntry,
    ExtensionsConfig,
    Hypervolt,
    Octopus,
    Schedule,
)

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


def _config_with_threshold_extension(price_limit_incl_vat: float) -> AppConfig:
    # price_limit_incl_vat=0 requires extensions.threshold to be configured
    # (issue #166's validator) -- this helper builds a config satisfying
    # that for the "fully defer to the dynamic threshold" scenarios.
    return AppConfig(
        octopus=Octopus(account_number="A-1", api_key="key"),
        hypervolt=Hypervolt(username="user", password="pass"),
        schedule=Schedule(
            total_charge_duration=1,
            price_limit_incl_vat=price_limit_incl_vat,
            update_every_mins=30,
            poll_every_secs=10,
        ),
        extensions=ExtensionsConfig(
            threshold=ExtensionEntry(name="behaviours/dynamic_charging_threshold")
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


async def test_scheduler_uses_the_static_limit_when_no_threshold_provider_is_configured(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # No threshold_provider passed at all (the default) -- proves this
    # feature is genuinely opt-in and doesn't change today's behaviour.
    # Also proves the computed limit is actually wired into
    # ScheduleBuilder.update_limit (the built schedule reflects it), and
    # that the rebuild log states the plain "static" source, distinct from
    # "static cap".
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(50, 0, _now)]  # 50p exc VAT -- under a 100p limit
    scheduler = Scheduler(
        _agile_client(prices),
        _config(price_limit_incl_vat=100),
    )

    with caplog.at_level(logging.INFO):
        scheduler.invalidate()
        await scheduler.update()

    assert len(scheduler.schedule) == 1
    assert any("source: static)" in r.message for r in caplog.records)
    assert not any("source: static cap)" in r.message for r in caplog.records)


async def test_scheduler_falls_back_to_the_static_limit_when_the_threshold_provider_has_no_fresh_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Distinct from the "no threshold provider configured at all" test above:
    # here a provider IS configured and wired through ExtensionWrapper.invoke(),
    # it just has nothing fresh this cycle -- proving the None actually
    # returned by invoke() flows through ThresholdPolicy to the static
    # fallback, not just that the static-only code path works when there's
    # no provider object to call in the first place.
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

    with caplog.at_level(logging.INFO):
        scheduler.invalidate()
        await scheduler.update()

    assert len(scheduler.schedule) == 1
    assert any("source: static)" in r.message for r in caplog.records)
    assert not any("source: static cap)" in r.message for r in caplog.records)


async def test_scheduler_logs_the_computed_threshold_to_two_decimal_places_on_rebuild(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The extension's own poll-time log already reports the threshold it
    # computed (2dp) -- this proves the scheduler's own rebuild log also
    # states which limit it actually built against, at the same precision,
    # for whichever cycle triggered the rebuild.
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(50, 0, _now)]
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_FixedThresholdProvider({}, value=38.891799950000006),
        kind="charging threshold",
    )
    scheduler = Scheduler(
        _agile_client(prices),
        _config(price_limit_incl_vat=100),
        threshold_provider=threshold_provider,
    )

    with caplog.at_level(logging.INFO):
        scheduler.invalidate()
        await scheduler.update()

    assert any("38.89" in r.message for r in caplog.records)


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


async def test_scheduler_logs_the_computed_threshold_on_a_new_prices_rebuild_too(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # _rebuild_on_new_prices() is a second, independent log call site --
    # matches the existing "second call site" test above for build
    # behaviour, but for the log line specifically: a regression that only
    # added the limit to the replug-triggered log would pass every other
    # test in this file silently.
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

    with caplog.at_level(logging.INFO):
        await scheduler._rebuild_on_new_prices()

    assert any("60.00" in r.message for r in caplog.records)


async def test_scheduler_retries_on_the_next_new_prices_cycle_after_a_cold_start_skip_with_unchanged_prices() -> (
    None
):
    # Regression (Copilot review, PR #168): _rebuild_on_new_prices must not
    # commit _time_until when the effective limit is None (cold start) --
    # otherwise an unchanged price horizon on the next cycle looks identical
    # to the one already "seen", so the method returns via the
    # "Agile prices unchanged" short-circuit before ever asking the
    # threshold provider again, leaving the car unscheduled indefinitely
    # even once the extension has a usable value.
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(30, 0, _now)]
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_ChangingThresholdProvider([None, 40.0]),
        kind="charging threshold",
    )
    scheduler = Scheduler(
        _agile_client(prices),  # same prices returned on every call
        _config_with_threshold_extension(price_limit_incl_vat=0),
        threshold_provider=threshold_provider,
    )

    await scheduler._rebuild_on_new_prices()  # cold start: no limit yet, skips
    assert scheduler.schedule == []

    await scheduler._rebuild_on_new_prices()  # same prices, extension now has a value

    assert len(scheduler.schedule) == 1  # 40p limit admits the 30p price


async def test_scheduler_skips_the_rebuild_when_the_static_limit_is_zero_and_no_threshold_value_is_available_yet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Cold start: price_limit_incl_vat=0 fully defers to the dynamic
    # threshold, but the provider hasn't produced a fresh value yet and
    # nothing is cached from a prior cycle. The scheduler must not crash
    # or build a schedule from a guessed limit -- it should leave the
    # schedule empty, warn about why, and retry on its own once the
    # extension actually produces a value (proven behaviourally below by
    # driving a second cycle, rather than asserting the private
    # _invalidated flag directly).
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(30, 0, _now)]
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_ChangingThresholdProvider([None, 40.0]),
        kind="charging threshold",
    )
    scheduler = Scheduler(
        _agile_client(prices),
        _config_with_threshold_extension(price_limit_incl_vat=0),
        threshold_provider=threshold_provider,
    )

    with caplog.at_level(logging.WARNING):
        scheduler.invalidate()
        await scheduler.update()

    assert scheduler.schedule == []
    assert any(r.levelname == "WARNING" for r in caplog.records)

    await scheduler.update()  # the extension now has a value -- retries on its own

    assert len(scheduler.schedule) == 1  # 40p limit admits the 30p price


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


async def test_scheduler_uses_the_replug_specific_success_log_prefix(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # _RebuildTrigger routes a per-trigger success_log_prefix through the
    # single shared _rebuild -- if replug's and new-prices' prefixes were
    # ever swapped, every other test here (which only asserts on the
    # source/value substring, not the prefix) would still pass silently.
    _now = datetime.now(tz=_UTC)
    scheduler = Scheduler(
        _agile_client([_half_hour_price(50, 0, _now)]),
        _config(price_limit_incl_vat=100),
    )

    with caplog.at_level(logging.INFO):
        await scheduler._rebuild_on_replug()

    assert any(
        "New Schedule created on car plugged in:" in r.message for r in caplog.records
    )
    assert not any("New schedule created:" in r.message for r in caplog.records)


async def test_scheduler_uses_the_new_prices_specific_success_log_prefix(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Mirrors the replug test above for the other trigger's prefix.
    _now = datetime.now(tz=_UTC)
    scheduler = Scheduler(
        _agile_client([_half_hour_price(50, 0, _now)]),
        _config(price_limit_incl_vat=100),
    )

    with caplog.at_level(logging.INFO):
        await scheduler._rebuild_on_new_prices()

    assert any("New schedule created:" in r.message for r in caplog.records)
    assert not any(
        "New Schedule created on car plugged in:" in r.message for r in caplog.records
    )


async def test_scheduler_warns_with_the_replug_specific_wording_when_no_prices_are_returned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # no_prices_warning is another per-trigger string threaded through the
    # shared _rebuild -- same swap risk as success_log_prefix above.
    scheduler = Scheduler(_agile_client([]), _config(price_limit_incl_vat=100))

    with caplog.at_level(logging.WARNING):
        await scheduler._rebuild_on_replug()

    assert any(
        "No Agile prices returned. Skipping schedule rebuild." in r.message
        for r in caplog.records
    )
    assert not any(
        "No Agile prices returned. Skipping schedule update." in r.message
        for r in caplog.records
    )


async def test_scheduler_warns_with_the_new_prices_specific_wording_when_no_prices_are_returned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scheduler = Scheduler(_agile_client([]), _config(price_limit_incl_vat=100))

    with caplog.at_level(logging.WARNING):
        await scheduler._rebuild_on_new_prices()

    assert any(
        "No Agile prices returned. Skipping schedule update." in r.message
        for r in caplog.records
    )
    assert not any(
        "No Agile prices returned. Skipping schedule rebuild." in r.message
        for r in caplog.records
    )


async def test_scheduler_warns_with_the_replug_specific_wording_when_no_threshold_value_is_available(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # no_threshold_warning is the fourth per-trigger string threaded through
    # the shared _rebuild -- same swap risk as the other three.
    _now = datetime.now(tz=_UTC)
    scheduler = Scheduler(
        _agile_client([_half_hour_price(50, 0, _now)]),
        _config_with_threshold_extension(price_limit_incl_vat=0),
    )

    with caplog.at_level(logging.WARNING):
        await scheduler._rebuild_on_replug()

    assert any(
        "Skipping schedule rebuild on car plugged in until it produces one."
        in r.message
        for r in caplog.records
    )
    assert not any(
        "Skipping schedule update until it produces one." in r.message
        for r in caplog.records
    )


async def test_scheduler_warns_with_the_new_prices_specific_wording_when_no_threshold_value_is_available(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _now = datetime.now(tz=_UTC)
    scheduler = Scheduler(
        _agile_client([_half_hour_price(50, 0, _now)]),
        _config_with_threshold_extension(price_limit_incl_vat=0),
    )

    with caplog.at_level(logging.WARNING):
        await scheduler._rebuild_on_new_prices()

    assert any(
        "Skipping schedule update until it produces one." in r.message
        for r in caplog.records
    )
    assert not any(
        "Skipping schedule rebuild on car plugged in until it produces one."
        in r.message
        for r in caplog.records
    )


async def test_scheduler_logs_the_replug_specific_exception_message_on_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # exception_message is the fourth per-trigger string threaded through
    # the shared _rebuild's try/except -- same swap risk as the others.
    _client = Mock(spec=AgileClient)
    _client.get_upcoming_prices = AsyncMock(side_effect=RuntimeError("boom"))
    scheduler = Scheduler(_client, _config(price_limit_incl_vat=100))

    with caplog.at_level(logging.ERROR):
        await scheduler._rebuild_on_replug()

    assert any(
        "Failed to rebuild schedule on car plugged in." in r.message
        for r in caplog.records
    )
    assert not any(
        "Failed to create charging schedule." in r.message for r in caplog.records
    )


async def test_scheduler_logs_the_new_prices_specific_exception_message_on_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _client = Mock(spec=AgileClient)
    _client.get_upcoming_prices = AsyncMock(side_effect=RuntimeError("boom"))
    scheduler = Scheduler(_client, _config(price_limit_incl_vat=100))

    with caplog.at_level(logging.ERROR):
        await scheduler._rebuild_on_new_prices()

    assert any(
        "Failed to create charging schedule." in r.message for r in caplog.records
    )
    assert not any(
        "Failed to rebuild schedule on car plugged in." in r.message
        for r in caplog.records
    )
