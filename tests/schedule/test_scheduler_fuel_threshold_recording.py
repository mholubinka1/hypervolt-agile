import logging
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
    _provider = Mock(spec=ExtensionWrapper)
    _provider.invoke = AsyncMock(side_effect=list(values))
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
                    "SELECT recorded_at, effective_limit_incl_vat, limit_source "
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
