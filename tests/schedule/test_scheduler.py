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


async def test_scheduler_uses_the_threshold_providers_fresh_value_instead_of_the_static_config(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Static price_limit_incl_vat is 100p -- if the dynamic provider's 5p
    # value weren't taking effect, every period below would still be under
    # the static limit and a session would still build. Also asserts the
    # rebuild log names "dynamic" as the source, since this is the
    # dynamic-wins-over-static case (5p <= 100p, no clamp).
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

    with caplog.at_level(logging.INFO):
        scheduler.invalidate()
        await scheduler.update()

    assert scheduler.schedule == []
    assert any("source: dynamic)" in r.message for r in caplog.records)


async def test_scheduler_clamps_the_dynamic_threshold_to_the_static_price_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # price_limit_incl_vat is an ultimate ceiling (ADR 0022): a dynamic value
    # ABOVE the static limit must not be allowed to raise the effective limit
    # past what the operator set. Static 20p incl VAT -> ~19.05p exc VAT;
    # dynamic 50p incl VAT -> ~47.62p exc VAT. A 30p exc-VAT price sits
    # between the two -- correctly clamped to the static limit, it's above
    # 19.05p (no session); if the dynamic value wrongly won, it would qualify
    # under 47.62p and build one.
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(30, 0, _now)]
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_FixedThresholdProvider({}, value=50.0),
        kind="charging threshold",
    )
    scheduler = Scheduler(
        _agile_client(prices),
        _config(price_limit_incl_vat=20),
        threshold_provider=threshold_provider,
    )

    with caplog.at_level(logging.INFO):
        scheduler.invalidate()
        await scheduler.update()

    assert scheduler.schedule == []
    assert any("source: static cap)" in r.message for r in caplog.records)


async def test_scheduler_falls_back_to_the_static_limit_when_the_threshold_provider_has_no_fresh_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Static price_limit_incl_vat is 100p -- a 50p price should still clear
    # it and build a session, proving a None from the provider means "use
    # the static config for this cycle", not "treat as a zero/blocking limit".
    # Also asserts the log states the plain "static" source (no clamp
    # happened -- there was nothing fresh to clamp against), and NOT the
    # distinct "static cap" source used when a clamp actually occurs.
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


async def test_scheduler_uses_the_static_limit_when_no_threshold_provider_is_configured(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # No threshold_provider passed at all (the default) -- proves this
    # feature is genuinely opt-in and doesn't change today's behaviour.
    # Also asserts the plain "static" source label, distinct from "static
    # cap" (see test above).
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


async def test_scheduler_fully_defers_to_the_dynamic_threshold_when_the_static_limit_is_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # price_limit_incl_vat=0 means "fully defer to the dynamic threshold
    # extension" (ADR 0022) -- the fresh 40p value should be used as-is
    # (no static value to clamp against), admitting a 30p exc-VAT price
    # that a lower static limit would otherwise block. The log should
    # state "dynamic" as the source.
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(30, 0, _now)]
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_FixedThresholdProvider({}, value=40.0),
        kind="charging threshold",
    )
    scheduler = Scheduler(
        _agile_client(prices),
        _config_with_threshold_extension(price_limit_incl_vat=0),
        threshold_provider=threshold_provider,
    )

    with caplog.at_level(logging.INFO):
        scheduler.invalidate()
        await scheduler.update()

    assert len(scheduler.schedule) == 1
    assert any("source: dynamic)" in r.message for r in caplog.records)


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


async def test_scheduler_uses_the_cached_dynamic_threshold_when_the_static_limit_is_zero_and_no_fresh_value_this_cycle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # price_limit_incl_vat=0 fully defers to the dynamic threshold. The
    # first cycle gets a fresh 40p value (admits the 30p price, caching
    # 40p). The second cycle's provider returns None (no fresh value) --
    # the cached 40p value should still be used, and the log should state
    # "cached dynamic" as the source.
    _now = datetime.now(tz=_UTC)
    _later = _now + timedelta(hours=2)
    _client = Mock(spec=AgileClient)
    _client.get_upcoming_prices = AsyncMock(
        side_effect=[
            [_half_hour_price(30, 0, _now)],
            [_half_hour_price(30, 0, _later)],
        ]
    )
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_ChangingThresholdProvider([40.0, None]),
        kind="charging threshold",
    )
    scheduler = Scheduler(
        _client,
        _config_with_threshold_extension(price_limit_incl_vat=0),
        threshold_provider=threshold_provider,
    )

    scheduler.invalidate()
    await scheduler.update()
    assert len(scheduler.schedule) == 1  # fresh 40p limit admits the 30p price

    with caplog.at_level(logging.INFO):
        await scheduler._rebuild_on_new_prices()

    assert len(scheduler.schedule) == 1  # cached 40p limit still admits it
    assert any("source: cached dynamic)" in r.message for r in caplog.records)


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


@pytest.mark.parametrize("invalid_value", [0.0, -5.0, float("inf"), float("nan")])
async def test_scheduler_ignores_a_non_finite_or_non_positive_dynamic_threshold(
    invalid_value: float,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Scheduler must not trust an arbitrary extension's return value as-is --
    # a misbehaving provider returning 0, negative, infinite, or NaN must be
    # treated the same as "no fresh value this cycle" (falling back to the
    # static limit here), not cached or used to compute an effective limit.
    _now = datetime.now(tz=_UTC)
    prices = [_half_hour_price(50, 0, _now)]  # 50p exc VAT -- under a 100p limit
    threshold_provider = ExtensionWrapper(
        name="fake_threshold",
        provider=_FixedThresholdProvider({}, value=invalid_value),
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

    assert len(scheduler.schedule) == 1  # static 100p limit used instead
    assert any("source: static)" in r.message for r in caplog.records)
    assert any(r.levelname == "WARNING" for r in caplog.records)


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
