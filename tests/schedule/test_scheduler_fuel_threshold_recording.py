import asyncio
import logging
import threading
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from common.extensions import ExtensionWrapper
from common.model import Price
from octopus.client import AgileClient
from schedule import Scheduler
from schedule.fuel_threshold_recorder import FuelThresholdRecorder
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from config import (
    AppConfig,
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
    [float("nan"), float("inf"), 10000.0, 9999.9996, -1.0, 0.0],
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


async def test_scheduler_stores_null_dynamic_threshold_but_keeps_the_limit_when_the_threshold_exceeds_the_column() -> (
    None
):
    engine = _in_memory_database()
    scheduler = _scheduler_with_provider(
        engine,
        _FuelPriceReportingProvider(threshold=1500.0, fuel_price=140.5),
        static_limit=100,
    )

    scheduler.invalidate()
    await scheduler.update()

    (row,) = _stored_rows(engine)
    assert row.dynamic_threshold_incl_vat is None
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


async def test_prepare_creates_the_table_without_a_record_call() -> None:
    engine = _in_memory_database()

    await FuelThresholdRecorder(engine).prepare()

    assert inspect(engine).has_table("fuel_price_threshold")


async def test_prepare_warns_without_raising_or_leaking_the_password_when_the_database_is_unreachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _password = "s3cret-db-pw"
    engine = create_engine(f"mysql+pymysql://user:{_password}@127.0.0.1:1/agile")

    with caplog.at_level(logging.WARNING):
        await FuelThresholdRecorder(engine).prepare()

    assert "OperationalError" in caplog.text
    assert _password not in caplog.text
