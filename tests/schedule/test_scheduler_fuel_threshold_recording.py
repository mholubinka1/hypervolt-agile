import asyncio
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from common.extensions import ExtensionWrapper
from common.model import Price
from octopus.client import AgileClient
from schedule import Scheduler
from schedule.fuel_threshold_recorder import FuelThresholdRecorder
from schedule.threshold_policy import EffectiveLimit
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from config import (
    AppConfig,
    DatabaseConfig,
    ExtensionEntry,
    ExtensionsConfig,
    Hypervolt,
    Octopus,
    Schedule,
)

_UTC = ZoneInfo("UTC")
_RECORDED_AT = datetime(2026, 11, 3, 12, 0, tzinfo=_UTC)


def _config(price_limit_incl_vat: float) -> AppConfig:
    _extensions = ExtensionsConfig(threshold=ExtensionEntry(name="fuel"))
    return AppConfig(
        extensions=_extensions,
        octopus=Octopus(account_number="A-1", api_key="key"),
        hypervolt=Hypervolt(username="user", password="pass"),
        schedule=Schedule(
            total_charge_duration=1,
            price_limit_incl_vat=price_limit_incl_vat,
            update_every_mins=30,
            poll_every_secs=10,
        ),
    )


def _agile_client() -> Mock:
    _start = datetime.now(tz=_UTC)
    _prices = [
        Price(
            value_exc_vat=50,
            valid_from=_start,
            valid_to=_start + timedelta(minutes=30),
        )
    ]
    _client = Mock(spec=AgileClient)
    _client.get_upcoming_prices = AsyncMock(return_value=_prices)
    return _client


def _in_memory_database() -> Engine:
    # StaticPool + check_same_thread=False so the recorder's worker thread
    # (asyncio.to_thread) sees the same in-memory database as the test.
    return create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


def _stored_rows(engine: Engine) -> list:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT recorded_at, effective_limit_incl_vat, limit_source, "
                    "fuel_price_pence_per_litre, dynamic_threshold_incl_vat "
                    "FROM fuel_price_threshold ORDER BY id"
                )
            )
        )


def _row_count(engine: Engine) -> int:
    if not inspect(engine).has_table("fuel_price_threshold"):
        return 0
    return len(_stored_rows(engine))


class _MutableFuelPriceProvider:
    def __init__(self, threshold: float | None, fuel_price: float | None) -> None:
        self.threshold = threshold
        self.fuel_price = fuel_price

    async def get_threshold(self) -> float | None:
        return self.threshold

    async def get_fuel_price(self) -> float | None:
        return self.fuel_price


def _scheduler_with_provider(
    engine: Engine, provider: object, static_limit: float
) -> Scheduler:
    return Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=static_limit),
        threshold_provider=ExtensionWrapper(
            name="fuel", provider=provider, kind="charging threshold"
        ),
        fuel_threshold_recorder=FuelThresholdRecorder(
            engine, clock=lambda: _RECORDED_AT
        ),
    )


def _thirty_one_minutes_later(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ThirtyOneMinutesLater(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return super().now(tz) + timedelta(minutes=31)

    monkeypatch.setattr("schedule.datetime", _ThirtyOneMinutesLater)


async def test_the_first_update_stores_the_fuel_price_threshold_effective_limit_and_source() -> (
    None
):
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _MutableFuelPriceProvider(threshold=20.0, fuel_price=140.5),
        static_limit=100,
    )

    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert str(row.recorded_at).startswith("2026-11-03 12:00:00")
    assert float(row.fuel_price_pence_per_litre) == 140.5
    assert float(row.dynamic_threshold_incl_vat) == 20
    assert float(row.effective_limit_incl_vat) == 20
    assert row.limit_source == "dynamic"


async def test_a_second_update_within_the_interval_writes_no_further_row() -> None:
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)

    for _ in range(3):
        await scheduler.update()

    assert _row_count(engine) == 1


async def test_scheduler_records_an_unchanged_fuel_price_again_once_the_update_interval_has_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)
    await scheduler.update()

    _thirty_one_minutes_later(monkeypatch)
    await scheduler.update()

    assert [float(row.fuel_price_pence_per_litre) for row in _stored_rows(engine)] == [
        150.0,
        150.0,
    ]


async def test_nothing_is_written_without_a_fuel_price_and_the_first_later_cycle_with_one_writes() -> (
    None
):
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=None)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)

    await scheduler.update()
    await scheduler.update()
    assert _row_count(engine) == 0

    provider.fuel_price = 150.0
    await scheduler.update()

    assert [float(row.fuel_price_pence_per_litre) for row in _stored_rows(engine)] == [
        150.0
    ]


@pytest.mark.parametrize("threshold", [None, float("nan"), 10_000_000.0])
async def test_nothing_is_written_when_the_threshold_beside_a_price_is_absent_or_unstorable_and_a_row_follows_once_it_is_usable(
    threshold: float | None,
) -> None:
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=threshold, fuel_price=140.5)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)

    await scheduler.update()
    assert _row_count(engine) == 0

    provider.threshold = 20.0
    await scheduler.update()

    assert [float(row.dynamic_threshold_incl_vat) for row in _stored_rows(engine)] == [
        20.0
    ]


async def test_nothing_is_written_while_the_static_limit_caps_the_dynamic_threshold() -> (
    None
):
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0),
        static_limit=10,
    )

    await scheduler.update()

    assert _row_count(engine) == 0


async def test_a_dynamic_row_follows_in_the_next_interval_once_the_threshold_is_back_under_the_static_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=10)
    await scheduler.update()
    provider.threshold = 8.0
    _thirty_one_minutes_later(monkeypatch)

    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert row.limit_source == "dynamic"
    assert float(row.effective_limit_incl_vat) == 8


async def test_a_large_dynamic_threshold_is_stored_as_the_limit_when_the_static_limit_defers_fully() -> (
    None
):
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _MutableFuelPriceProvider(threshold=1500.0, fuel_price=140.5),
        static_limit=0,
    )

    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert float(row.effective_limit_incl_vat) == 1500
    assert float(row.dynamic_threshold_incl_vat) == 1500


@pytest.mark.parametrize("trigger", ["replug", "new_prices"])
async def test_a_schedule_rebuild_with_no_due_interval_writes_no_row(
    trigger: str,
) -> None:
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)
    await scheduler.update()

    if trigger == "replug":
        scheduler.invalidate()
        await scheduler.update()
    else:
        await scheduler._rebuild_on_new_prices()

    assert _row_count(engine) == 1


async def test_scheduler_without_a_recorder_still_builds_the_schedule() -> None:
    scheduler = Scheduler(_agile_client(), _config(price_limit_incl_vat=100))

    scheduler.invalidate()
    await scheduler.update()

    assert scheduler.schedule


async def test_scheduler_with_a_recorder_but_no_threshold_provider_records_nothing() -> (
    None
):
    engine = _in_memory_database()
    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=100),
        fuel_threshold_recorder=FuelThresholdRecorder(
            engine, clock=lambda: _RECORDED_AT
        ),
    )

    scheduler.invalidate()
    await scheduler.update()

    assert scheduler.schedule
    assert _row_count(engine) == 0


async def test_recording_into_an_existing_table_keeps_the_earlier_rows() -> None:
    engine = _in_memory_database()
    for _ in range(2):
        scheduler = _scheduler_with_provider(
            engine,
            _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0),
            static_limit=100,
        )
        await scheduler.update()

    assert _row_count(engine) == 2


async def test_scheduler_still_builds_the_schedule_and_hides_the_password_when_the_database_is_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _password = "s3cret-db-pw"
    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=100),
        threshold_provider=ExtensionWrapper(
            name="fuel",
            provider=_MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0),
            kind="charging threshold",
        ),
        fuel_threshold_recorder=FuelThresholdRecorder(
            create_engine(f"mysql+pymysql://user:{_password}@127.0.0.1:1/agile")
        ),
    )

    with caplog.at_level(logging.WARNING):
        scheduler.invalidate()
        await scheduler.update()

    assert "OperationalError" in caplog.text
    assert "2003" in caplog.text
    assert _password not in caplog.text


async def test_scheduler_warns_and_carries_on_when_the_database_hangs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _release = threading.Event()

    def _blocked_connection() -> None:
        _release.wait(timeout=30)
        raise RuntimeError("released")

    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=100),
        threshold_provider=ExtensionWrapper(
            name="fuel",
            provider=_MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0),
            kind="charging threshold",
        ),
        fuel_threshold_recorder=FuelThresholdRecorder(
            create_engine("sqlite://", creator=_blocked_connection),
            timeout_secs=0.05,
        ),
    )

    try:
        with caplog.at_level(logging.WARNING):
            scheduler.invalidate()
            await asyncio.wait_for(scheduler.update(), timeout=5)
    finally:
        _release.set()

    assert "timed out" in caplog.text


async def test_a_failing_database_is_attempted_once_per_interval_not_once_per_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _connection_attempts: list[int] = []

    def _failing_connection() -> None:
        _connection_attempts.append(1)
        raise sqlite3.OperationalError("database is down")

    scheduler = _scheduler_with_provider(
        create_engine("sqlite://", creator=_failing_connection),
        _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0),
        static_limit=100,
    )
    for _ in range(4):
        await scheduler.update()
    assert len(_connection_attempts) == 1

    _thirty_one_minutes_later(monkeypatch)
    await scheduler.update()

    assert len(_connection_attempts) == 2


async def test_a_non_number_fuel_price_writes_no_row_and_does_not_fail_the_cycle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _MutableFuelPriceProvider(threshold=20.0, fuel_price="unavailable"),  # type: ignore[arg-type]
        static_limit=100,
    )

    with caplog.at_level(logging.WARNING):
        await scheduler.update()

    assert _row_count(engine) == 0
    assert "Failed to" not in caplog.text


class _ThresholdOnlyProvider:
    async def get_threshold(self) -> float | None:
        return 20.0


class _FuelPriceRaisingProvider(_ThresholdOnlyProvider):
    async def get_fuel_price(self) -> float | None:
        raise RuntimeError("boom")


@pytest.mark.parametrize(
    "provider", [_ThresholdOnlyProvider(), _FuelPriceRaisingProvider()]
)
async def test_nothing_is_written_when_the_extension_cannot_report_a_fuel_price(
    provider: object,
) -> None:
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)

    await scheduler.update()

    assert _row_count(engine) == 0


class _SwitchableFuelPriceProvider:
    def __init__(self, threshold: float, fuel_price: float) -> None:
        self.threshold = threshold
        self.fuel_price = fuel_price
        self.hangs = False
        self.fuel_price_calls = 0
        self.hangs_on_threshold = False
        self.threshold_calls = 0

    async def get_threshold(self) -> float:
        self.threshold_calls += 1
        if self.hangs_on_threshold:
            await asyncio.Event().wait()
        return self.threshold

    async def get_fuel_price(self) -> float:
        self.fuel_price_calls += 1
        if self.hangs:
            await asyncio.Event().wait()
        return self.fuel_price


async def test_a_hung_fuel_price_accessor_times_out_and_is_not_called_again_within_the_interval(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("schedule._FUEL_PRICE_TIMEOUT_SECS", 0.05)
    provider = _SwitchableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    provider.hangs = True
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)

    with caplog.at_level(logging.WARNING):
        for _ in range(4):
            await scheduler.update()

    assert provider.fuel_price_calls == 1
    assert caplog.text.count("did not report a fuel price") == 1
    assert _row_count(engine) == 0


async def test_recording_resumes_once_the_interval_after_a_timeout_has_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("schedule._FUEL_PRICE_TIMEOUT_SECS", 0.05)
    engine = _in_memory_database()
    provider = _SwitchableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    provider.hangs = True
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)
    await scheduler.update()
    provider.hangs = False

    await scheduler.update()  # still inside the interval
    assert _row_count(engine) == 0

    _thirty_one_minutes_later(monkeypatch)
    await scheduler.update()

    assert _row_count(engine) == 1


async def test_a_hung_threshold_accessor_times_out_and_is_not_called_again_within_the_interval(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("schedule._FUEL_PRICE_TIMEOUT_SECS", 0.05)
    provider = _SwitchableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    provider.fuel_price = None  # type: ignore[assignment]
    scheduler = _scheduler_with_provider(
        _in_memory_database(), provider, static_limit=100
    )
    await scheduler.update()  # builds the schedule; no price yet, so no record
    provider.fuel_price = 150.0
    provider.hangs_on_threshold = True
    calls_before_the_hang = provider.threshold_calls

    with caplog.at_level(logging.WARNING):
        # A bounded wait: an unbounded accessor call would hang this test.
        await asyncio.wait_for(scheduler.update(), timeout=2)
        await asyncio.wait_for(scheduler.update(), timeout=2)

    assert provider.threshold_calls == calls_before_the_hang + 1
    assert caplog.text.count("did not report a threshold") == 1


class _BrokenRecorder(FuelThresholdRecorder):
    async def record(self, *args: object, **kwargs: object) -> bool:  # type: ignore[override]
        raise RuntimeError("unexpected failure")


async def test_an_unexpected_failure_in_the_record_step_does_not_escape_update(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=100),
        threshold_provider=ExtensionWrapper(
            name="fuel",
            provider=_MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0),
            kind="charging threshold",
        ),
        fuel_threshold_recorder=_BrokenRecorder(_in_memory_database()),
    )

    with caplog.at_level(logging.ERROR):
        await scheduler.update()

    assert "record the fuel price threshold" in caplog.text


async def test_record_is_timestamped_before_the_slow_first_time_table_creation() -> (
    None
):
    engine = _in_memory_database()
    _table_existed_when_stamped: list[bool] = []

    def _clock() -> datetime:
        _table_existed_when_stamped.append(
            inspect(engine).has_table("fuel_price_threshold")
        )
        return _RECORDED_AT

    await FuelThresholdRecorder(engine, clock=_clock).record(
        EffectiveLimit(20.0, 19.0, "dynamic"),
        fuel_price_pence_per_litre=150.0,
        dynamic_threshold_incl_vat=20.0,
    )

    assert _table_existed_when_stamped == [False]


async def test_prepare_creates_the_table_without_a_record_call() -> None:
    engine = _in_memory_database()

    await FuelThresholdRecorder(engine).prepare()

    assert inspect(engine).has_table("fuel_price_threshold")


def test_recorder_built_from_config_connects_to_the_configured_database_with_bounded_timeouts() -> (
    None
):
    database = DatabaseConfig(
        host="db.local",
        port=3307,
        user="energy-monitor",
        password="p@ss/w:rd",
        name="home_monitoring",
    )

    with patch("schedule.fuel_threshold_recorder.create_engine") as create_engine:
        FuelThresholdRecorder.from_config(database)

    (_url,), _options = create_engine.call_args
    assert _url.drivername == "mysql+pymysql"
    assert (_url.host, _url.port) == ("db.local", 3307)
    assert (_url.username, _url.password) == ("energy-monitor", "p@ss/w:rd")
    assert _url.database == "home_monitoring"
    assert _options["pool_pre_ping"] is True
    assert _options["connect_args"] == {
        "connect_timeout": 5,
        "read_timeout": 5,
        "write_timeout": 5,
    }


async def test_prepare_warns_without_raising_or_leaking_the_password_when_the_database_is_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _password = "s3cret-db-pw"
    engine = create_engine(f"mysql+pymysql://user:{_password}@127.0.0.1:1/agile")

    with caplog.at_level(logging.WARNING):
        await FuelThresholdRecorder(engine).prepare()

    assert "OperationalError" in caplog.text
    assert _password not in caplog.text


async def test_nothing_is_written_when_no_limit_resolves_because_the_static_limit_defers_to_a_missing_threshold() -> (
    None
):
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _MutableFuelPriceProvider(threshold=None, fuel_price=140.5),
        static_limit=0,
    )

    await scheduler.update()

    assert _row_count(engine) == 0
