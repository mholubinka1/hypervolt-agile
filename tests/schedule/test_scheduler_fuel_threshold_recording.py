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


def _threshold_provider(*values: float | None) -> Mock:
    _remaining_thresholds = iter(values)

    async def _answer(method_name: str) -> float | None:
        if method_name == "get_threshold":
            return next(_remaining_thresholds)
        return None

    _provider = Mock(spec=ExtensionWrapper)
    _provider.invoke = AsyncMock(side_effect=_answer)
    return _provider


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


async def test_scheduler_stores_the_effective_limit_and_its_source_when_a_recorder_is_configured() -> (
    None
):
    engine = _in_memory_database()
    recorder = FuelThresholdRecorder(engine, clock=lambda: _RECORDED_AT)
    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=100),
        fuel_threshold_recorder=recorder,
    )

    scheduler.invalidate()
    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert str(row.recorded_at).startswith("2026-11-03 12:00:00")
    assert float(row.effective_limit_incl_vat) == 100
    assert row.limit_source == "static"


async def test_scheduler_without_a_recorder_still_builds_the_schedule() -> None:
    scheduler = Scheduler(_agile_client(), _config(price_limit_incl_vat=100))

    scheduler.invalidate()
    await scheduler.update()

    assert scheduler.schedule


async def test_scheduler_still_builds_the_schedule_and_hides_the_password_when_the_database_is_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _password = "s3cret-db-pw"
    engine = create_engine(f"mysql+pymysql://user:{_password}@127.0.0.1:1/agile")
    recorder = FuelThresholdRecorder(engine)
    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=100),
        fuel_threshold_recorder=recorder,
    )

    with caplog.at_level(logging.WARNING):
        scheduler.invalidate()
        await scheduler.update()

    assert scheduler.schedule
    assert "OperationalError" in caplog.text
    assert "2003" in caplog.text
    assert _password not in caplog.text


async def test_scheduler_records_nothing_when_no_limit_is_resolved_on_a_cold_start() -> (
    None
):
    engine = _in_memory_database()
    recorder = FuelThresholdRecorder(engine, clock=lambda: _RECORDED_AT)
    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=0),
        threshold_provider=_threshold_provider(None),
        fuel_threshold_recorder=recorder,
    )

    scheduler.invalidate()
    await scheduler.update()

    assert not inspect(engine).has_table("fuel_price_threshold")


async def test_recording_into_an_existing_table_keeps_the_earlier_rows() -> None:
    engine = _in_memory_database()
    for _ in range(2):
        scheduler = Scheduler(
            _agile_client(),
            _config(price_limit_incl_vat=100),
            fuel_threshold_recorder=FuelThresholdRecorder(
                engine, clock=lambda: _RECORDED_AT
            ),
        )
        scheduler.invalidate()
        await scheduler.update()

    assert len(_stored_rows(engine)) == 2


@pytest.mark.parametrize(
    ("static_limit", "provider_values", "expected_limit", "expected_source"),
    [
        (100, [], 100, "static"),
        (100, [20.0], 20, "dynamic"),
        (10, [20.0], 10, "static cap"),
        (0, [20.0, None], 20, "cached dynamic"),
    ],
)
async def test_scheduler_stores_which_source_produced_the_effective_limit(
    static_limit: float,
    provider_values: list[float | None],
    expected_limit: float,
    expected_source: str,
) -> None:
    engine = _in_memory_database()
    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=static_limit),
        threshold_provider=(
            _threshold_provider(*provider_values) if provider_values else None
        ),
        fuel_threshold_recorder=FuelThresholdRecorder(
            engine, clock=lambda: _RECORDED_AT
        ),
    )

    for _ in provider_values or [None]:
        scheduler.invalidate()
        await scheduler.update()

    _last = _stored_rows(engine)[-1]
    assert float(_last.effective_limit_incl_vat) == expected_limit
    assert _last.limit_source == expected_source


class _FuelPriceReportingProvider:
    def __init__(self, threshold: float | None, fuel_price: float | None) -> None:
        self._threshold = threshold
        self._fuel_price = fuel_price

    async def get_threshold(self) -> float | None:
        return self._threshold

    async def get_fuel_price(self) -> float | None:
        return self._fuel_price


async def test_scheduler_stores_the_fuel_price_and_dynamic_threshold_alongside_the_effective_limit() -> (
    None
):
    engine = _in_memory_database()
    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=100),
        threshold_provider=ExtensionWrapper(
            name="fuel",
            provider=_FuelPriceReportingProvider(threshold=20.0, fuel_price=140.5),
            kind="charging threshold",
        ),
        fuel_threshold_recorder=FuelThresholdRecorder(
            engine, clock=lambda: _RECORDED_AT
        ),
    )

    scheduler.invalidate()
    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert float(row.fuel_price_pence_per_litre) == 140.5
    assert float(row.dynamic_threshold_incl_vat) == 20
    assert float(row.effective_limit_incl_vat) == 20
    assert row.limit_source == "dynamic"


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


async def test_scheduler_stores_null_price_and_threshold_with_a_static_source_when_the_poll_failed_and_the_cache_cleared() -> (
    None
):
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _FuelPriceReportingProvider(threshold=None, fuel_price=None),
        static_limit=100,
    )

    scheduler.invalidate()
    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert row.fuel_price_pence_per_litre is None
    assert row.dynamic_threshold_incl_vat is None
    assert row.limit_source == "static"


async def test_scheduler_stores_null_price_and_threshold_with_a_cached_dynamic_source_when_the_poll_failed() -> (
    None
):
    engine = _in_memory_database()
    provider = _FuelPriceReportingProvider(threshold=20.0, fuel_price=140.5)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=0)
    scheduler.invalidate()
    await scheduler.update()

    provider._threshold = None
    provider._fuel_price = None
    scheduler.invalidate()
    await scheduler.update()

    _last = _stored_rows(engine)[-1]
    assert _last.fuel_price_pence_per_litre is None
    assert _last.dynamic_threshold_incl_vat is None
    assert _last.limit_source == "cached dynamic"


class _ThresholdOnlyProvider:
    async def get_threshold(self) -> float | None:
        return 20.0


class _FuelPriceHangingProvider(_ThresholdOnlyProvider):
    async def get_fuel_price(self) -> float | None:
        await asyncio.Event().wait()
        return None


async def test_scheduler_builds_the_schedule_and_stores_a_null_fuel_price_when_the_fuel_price_hangs(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("schedule._FUEL_PRICE_TIMEOUT_SECS", 0.05)
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine, _FuelPriceHangingProvider(), static_limit=100
    )

    with caplog.at_level(logging.WARNING):
        scheduler.invalidate()
        await asyncio.wait_for(scheduler.update(), timeout=5)

    (row,) = _stored_rows(engine)
    assert row.fuel_price_pence_per_litre is None
    assert float(row.dynamic_threshold_incl_vat) == 20
    assert float(row.effective_limit_incl_vat) == 20
    assert row.limit_source == "dynamic"
    assert "'fuel'" in caplog.text
    assert "without a fuel price" in caplog.text


class _FuelPriceRaisingProvider(_ThresholdOnlyProvider):
    async def get_fuel_price(self) -> float | None:
        raise RuntimeError("boom")


@pytest.mark.parametrize(
    "provider", [_ThresholdOnlyProvider(), _FuelPriceRaisingProvider()]
)
async def test_scheduler_still_stores_the_row_with_a_null_fuel_price_when_the_extension_cannot_report_one(
    provider: object,
) -> None:
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)

    scheduler.invalidate()
    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert row.fuel_price_pence_per_litre is None
    assert float(row.dynamic_threshold_incl_vat) == 20
    assert row.limit_source == "dynamic"


async def test_scheduler_builds_the_schedule_and_warns_when_the_database_hangs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _release = threading.Event()

    def _blocked_connection() -> None:
        _release.wait(timeout=30)
        raise RuntimeError("released")

    engine = create_engine("sqlite://", creator=_blocked_connection)
    recorder = FuelThresholdRecorder(engine, timeout_secs=0.05)
    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=100),
        fuel_threshold_recorder=recorder,
    )

    try:
        with caplog.at_level(logging.WARNING):
            scheduler.invalidate()
            await asyncio.wait_for(scheduler.update(), timeout=5)
    finally:
        _release.set()

    assert scheduler.schedule
    assert "timed out" in caplog.text


@pytest.mark.parametrize(
    "fuel_price",
    # 9999.9996 is under the bound but rounds up to 10000.000 in the column.
    # 10**400 is a valid int that float() cannot represent.
    [float("nan"), float("inf"), 10000.0, 9999.9996, -1.0, 0.0, 10**400],
)
async def test_scheduler_stores_null_fuel_price_but_keeps_the_limit_when_the_price_is_unusable(
    fuel_price: float,
) -> None:
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _FuelPriceReportingProvider(threshold=20.0, fuel_price=fuel_price),
        static_limit=100,
    )

    scheduler.invalidate()
    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert row.fuel_price_pence_per_litre is None
    assert float(row.effective_limit_incl_vat) == 20
    assert row.limit_source == "dynamic"


async def test_scheduler_stores_a_large_dynamic_threshold_as_the_limit_when_the_static_limit_defers_fully() -> (
    None
):
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _FuelPriceReportingProvider(threshold=1500.0, fuel_price=140.5),
        static_limit=0,
    )

    scheduler.invalidate()
    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert float(row.effective_limit_incl_vat) == 1500
    assert float(row.dynamic_threshold_incl_vat) == 1500
    assert float(row.fuel_price_pence_per_litre) == 140.5
    assert row.limit_source == "dynamic"


async def test_scheduler_stores_null_threshold_and_null_fuel_price_but_keeps_the_limit_when_the_threshold_exceeds_the_column() -> (
    None
):
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _FuelPriceReportingProvider(threshold=10_000_000.0, fuel_price=140.5),
        static_limit=100,
    )

    scheduler.invalidate()
    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert row.dynamic_threshold_incl_vat is None
    assert row.fuel_price_pence_per_litre is None
    assert float(row.effective_limit_incl_vat) == 100
    assert row.limit_source == "static cap"


async def test_scheduler_stores_null_fuel_price_without_failing_the_rebuild_when_the_extension_returns_a_non_number(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _FuelPriceReportingProvider(threshold=20.0, fuel_price="unavailable"),  # type: ignore[arg-type]
        static_limit=100,
    )

    with caplog.at_level(logging.WARNING):
        scheduler.invalidate()
        await scheduler.update()

    (row,) = _stored_rows(engine)
    assert row.fuel_price_pence_per_litre is None
    assert float(row.effective_limit_incl_vat) == 20
    assert "Failed to create" not in caplog.text


async def test_scheduler_records_on_a_new_prices_rebuild_as_well_as_a_replug_rebuild() -> (
    None
):
    # _rebuild_on_new_prices() is a second, independent trigger into the same
    # _rebuild() -- a regression that wired recording into only the replug
    # trigger would pass every other test in this file.
    _now = datetime.now(tz=_UTC)
    _later = _now + timedelta(hours=2)
    _client = Mock(spec=AgileClient)
    _client.get_upcoming_prices = AsyncMock(
        side_effect=[
            [
                Price(
                    value_exc_vat=50,
                    valid_from=_now,
                    valid_to=_now + timedelta(minutes=30),
                )
            ],
            [
                Price(
                    value_exc_vat=50,
                    valid_from=_later,
                    valid_to=_later + timedelta(minutes=30),
                )
            ],
        ]
    )
    engine = _in_memory_database()
    scheduler = Scheduler(
        _client,
        _config(price_limit_incl_vat=100),
        fuel_threshold_recorder=FuelThresholdRecorder(
            engine, clock=lambda: _RECORDED_AT
        ),
    )

    scheduler.invalidate()
    await scheduler.update()
    await scheduler._rebuild_on_new_prices()

    assert len(_stored_rows(engine)) == 2


async def test_scheduler_stores_no_fuel_price_beside_a_missing_dynamic_threshold() -> (
    None
):
    # A third-party extension whose threshold was rejected or absent but whose
    # price accessor still answers must not leave a price next to a NULL
    # threshold in the same row.
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _FuelPriceReportingProvider(threshold=None, fuel_price=140.5),
        static_limit=100,
    )

    scheduler.invalidate()
    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert row.dynamic_threshold_incl_vat is None
    assert row.fuel_price_pence_per_litre is None


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
        EffectiveLimit(20.0, 19.0, "dynamic")
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


class _MutableFuelPriceProvider:
    def __init__(self, threshold: float | None, fuel_price: float | None) -> None:
        self.threshold = threshold
        self.fuel_price = fuel_price

    async def get_threshold(self) -> float | None:
        return self.threshold

    async def get_fuel_price(self) -> float | None:
        return self.fuel_price


async def test_scheduler_records_a_new_row_when_the_fuel_price_changes_between_rebuilds() -> (
    None
):
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)

    await scheduler.update()  # first call: the due rebuild records row 1
    provider.fuel_price = 151.25
    provider.threshold = 21.0
    await scheduler.update()  # no rebuild is due: only a price change can write

    assert [
        (
            float(row.fuel_price_pence_per_litre),
            float(row.dynamic_threshold_incl_vat),
            float(row.effective_limit_incl_vat),
            row.limit_source,
        )
        for row in _stored_rows(engine)
    ] == [(150.0, 20.0, 20.0, "dynamic"), (151.25, 21.0, 21.0, "dynamic")]


async def test_scheduler_writes_no_further_rows_while_the_fuel_price_stays_the_same() -> (
    None
):
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)

    for _ in range(3):
        await scheduler.update()

    assert len(_stored_rows(engine)) == 1


async def test_scheduler_writes_nothing_when_the_fuel_price_moves_by_less_than_a_tenth_of_a_penny() -> (
    None
):
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)
    await scheduler.update()

    provider.fuel_price = 150.0004
    await scheduler.update()

    assert len(_stored_rows(engine)) == 1


async def test_scheduler_records_the_static_cap_as_the_source_when_the_price_changes_under_a_static_cap() -> (
    None
):
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=10)
    await scheduler.update()

    provider.fuel_price = 151.0
    provider.threshold = 21.0
    await scheduler.update()

    _last = _stored_rows(engine)[-1]
    assert float(_last.fuel_price_pence_per_litre) == 151.0
    assert float(_last.dynamic_threshold_incl_vat) == 21.0
    assert float(_last.effective_limit_incl_vat) == 10
    assert _last.limit_source == "static cap"


async def test_scheduler_does_not_count_the_same_price_returning_after_an_outage_as_a_change() -> (
    None
):
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)
    await scheduler.update()

    provider.fuel_price = None
    await scheduler.update()
    provider.fuel_price = 150.0
    await scheduler.update()

    assert len(_stored_rows(engine)) == 1


async def test_scheduler_writes_one_row_when_a_rebuild_and_a_price_change_share_a_cycle() -> (
    None
):
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)
    await scheduler.update()

    provider.fuel_price = 151.0
    scheduler.invalidate()
    await scheduler.update()

    assert len(_stored_rows(engine)) == 2


async def test_scheduler_attempts_a_failing_database_once_per_price_change_not_once_per_cycle() -> (
    None
):
    _connection_attempts: list[int] = []

    def _failing_connection() -> None:
        _connection_attempts.append(1)
        raise sqlite3.OperationalError("database is down")

    engine = create_engine("sqlite://", creator=_failing_connection)
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = Scheduler(
        _agile_client(),
        _config(price_limit_incl_vat=100),
        threshold_provider=ExtensionWrapper(
            name="fuel", provider=provider, kind="charging threshold"
        ),
        fuel_threshold_recorder=FuelThresholdRecorder(engine),
    )
    await scheduler.update()
    _after_first_cycle = len(_connection_attempts)

    provider.fuel_price = 151.0
    for _ in range(4):
        await scheduler.update()

    assert len(_connection_attempts) == _after_first_cycle + 1


async def test_scheduler_writes_the_row_once_the_dynamic_threshold_appears_after_the_price() -> (
    None
):
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)
    await scheduler.update()

    provider.fuel_price = 151.0
    provider.threshold = None
    await scheduler.update()
    assert len(_stored_rows(engine)) == 1

    provider.threshold = 21.0
    await scheduler.update()

    assert float(_stored_rows(engine)[-1].fuel_price_pence_per_litre) == 151.0
    assert len(_stored_rows(engine)) == 2


async def test_scheduler_with_a_recorder_but_no_threshold_provider_checks_nothing_for_price_changes() -> (
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
    await scheduler.update()

    await scheduler.update()

    assert len(_stored_rows(engine)) == 1


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


async def test_a_hung_fuel_price_accessor_is_not_called_again_within_the_update_interval(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("schedule._FUEL_PRICE_TIMEOUT_SECS", 0.05)
    provider = _SwitchableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(
        _in_memory_database(), provider, static_limit=100
    )
    await scheduler.update()  # first call: the due rebuild, accessor answers
    provider.hangs = True
    calls_before_the_hang = provider.fuel_price_calls

    with caplog.at_level(logging.WARNING):
        await scheduler.update()  # the change check hits the hang and times out
        await scheduler.update()  # inside the backoff: accessor is left alone
        await scheduler.update()

    assert provider.fuel_price_calls == calls_before_the_hang + 1
    assert caplog.text.count("did not report a fuel price") == 1


async def test_price_change_recording_resumes_once_the_backoff_interval_has_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("schedule._FUEL_PRICE_TIMEOUT_SECS", 0.05)
    engine = _in_memory_database()
    provider = _SwitchableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)
    await scheduler.update()
    provider.hangs = True
    await scheduler.update()  # times out and backs off for one update interval
    provider.hangs = False
    provider.fuel_price = 151.0

    await scheduler.update()  # still inside the backoff
    assert len(_stored_rows(engine)) == 1

    class _ThirtyOneMinutesLater(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return super().now(tz) + timedelta(minutes=31)

    monkeypatch.setattr("schedule.datetime", _ThirtyOneMinutesLater)
    await scheduler.update()

    assert [float(row.fuel_price_pence_per_litre) for row in _stored_rows(engine)] == [
        150.0,
        151.0,
    ]


async def test_a_fuel_price_timeout_during_a_rebuild_also_backs_off_the_change_check(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("schedule._FUEL_PRICE_TIMEOUT_SECS", 0.05)
    provider = _SwitchableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    provider.hangs = True
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)

    with caplog.at_level(logging.WARNING):
        await scheduler.update()  # the rebuild's own fetch hangs
        await scheduler.update()

    assert provider.fuel_price_calls == 1
    assert [row.fuel_price_pence_per_litre for row in _stored_rows(engine)] == [None]


async def test_a_hung_threshold_accessor_does_not_stall_the_price_change_check(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("schedule._FUEL_PRICE_TIMEOUT_SECS", 0.05)
    provider = _SwitchableFuelPriceProvider(threshold=20.0, fuel_price=150.0)
    scheduler = _scheduler_with_provider(
        _in_memory_database(), provider, static_limit=100
    )
    await scheduler.update()
    provider.hangs_on_threshold = True
    provider.fuel_price = 151.0
    calls_before_the_hang = provider.threshold_calls

    with caplog.at_level(logging.WARNING):
        # A bounded wait: an unbounded accessor call would hang this test.
        await asyncio.wait_for(scheduler.update(), timeout=2)
        await asyncio.wait_for(scheduler.update(), timeout=2)

    assert provider.threshold_calls == calls_before_the_hang + 1
    assert caplog.text.count("did not report a threshold") == 1


class _BrokenChangeCheckRecorder(FuelThresholdRecorder):
    def fuel_price_changed(
        self, fuel_price: object, dynamic_threshold_incl_vat: object
    ) -> bool:
        raise RuntimeError("unexpected failure")


async def test_an_unexpected_failure_in_the_price_change_check_does_not_escape_update(
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
        fuel_threshold_recorder=_BrokenChangeCheckRecorder(_in_memory_database()),
    )

    with caplog.at_level(logging.ERROR):
        await scheduler.update()

    assert "fuel price change" in caplog.text


async def test_the_first_price_to_appear_after_a_price_less_rebuild_is_recorded() -> (
    None
):
    # The cold-start sequence seen in production: the rebuild runs before the
    # extension's first poll, so its row has no price.
    engine = _in_memory_database()
    provider = _MutableFuelPriceProvider(threshold=20.0, fuel_price=None)
    scheduler = _scheduler_with_provider(engine, provider, static_limit=100)
    await scheduler.update()
    provider.fuel_price = 150.0

    await scheduler.update()

    assert [
        (
            None
            if row.fuel_price_pence_per_litre is None
            else float(row.fuel_price_pence_per_litre)
        )
        for row in _stored_rows(engine)
    ] == [None, 150.0]
