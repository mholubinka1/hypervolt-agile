from unittest.mock import Mock

import pytest
from schedule.fuel_threshold_recorder import FuelThresholdRecorder
from schedule.threshold_policy import EffectiveLimit
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.pool import StaticPool

_LIMIT = EffectiveLimit(20.0, 19.0, "dynamic")


def _engine() -> Engine:
    return create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


def _prices(engine: Engine) -> list[float]:
    with engine.connect() as connection:
        return [
            float(row[0])
            for row in connection.execute(
                text("SELECT fuel_price_pence_per_litre FROM fuel_price_threshold")
            )
        ]


async def test_a_storable_price_is_written_and_reported_as_attempted() -> None:
    engine = _engine()

    attempted = await FuelThresholdRecorder(engine).record(
        _LIMIT, fuel_price_pence_per_litre=150.0, dynamic_threshold_incl_vat=20.0
    )

    assert attempted is True
    assert _prices(engine) == [150.0]


# 9999.9996 is under the bound but rounds up to 10000.000 in the column.
# 10**400 is a valid int that float() cannot represent.
@pytest.mark.parametrize(
    "fuel_price",
    [None, float("nan"), float("inf"), 10000.0, 9999.9996, -1.0, 0.0, 10**400],
)
async def test_an_unstorable_fuel_price_writes_nothing_and_is_reported_as_skipped(
    fuel_price: object,
) -> None:
    engine = _engine()

    attempted = await FuelThresholdRecorder(engine).record(
        _LIMIT,
        fuel_price_pence_per_litre=fuel_price,  # type: ignore[arg-type]
        dynamic_threshold_incl_vat=20.0,
    )

    assert attempted is False
    assert not inspect(engine).has_table("fuel_price_threshold")


@pytest.mark.parametrize("threshold", [None, float("nan"), 10_000_000.0])
async def test_a_price_beside_an_unstorable_threshold_is_skipped(
    threshold: object,
) -> None:
    engine = _engine()

    attempted = await FuelThresholdRecorder(engine).record(
        _LIMIT,
        fuel_price_pence_per_litre=150.0,
        dynamic_threshold_incl_vat=threshold,  # type: ignore[arg-type]
    )

    assert attempted is False


def test_a_disposed_recorder_releases_its_database_connections() -> None:
    engine = Mock(spec=Engine)

    FuelThresholdRecorder(engine).dispose()

    engine.dispose.assert_called_once_with()
