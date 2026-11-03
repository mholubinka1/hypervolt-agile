import pytest
from schedule.fuel_threshold_recorder import FuelThresholdRecorder
from schedule.threshold_policy import EffectiveLimit
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

_LIMIT = EffectiveLimit(20.0, 19.0, "dynamic")
_THRESHOLD = 20.0


def _recorder() -> FuelThresholdRecorder:
    return FuelThresholdRecorder(
        create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    )


def test_the_first_usable_fuel_price_is_a_change() -> None:
    assert _recorder().fuel_price_changed(150.0, _THRESHOLD)


async def test_the_fuel_price_just_recorded_is_not_a_change() -> None:
    _recorder_under_test = _recorder()
    await _recorder_under_test.record(
        _LIMIT,
        fuel_price_pence_per_litre=150.0,
        dynamic_threshold_incl_vat=_THRESHOLD,
    )

    assert not _recorder_under_test.fuel_price_changed(150.0, _THRESHOLD)


async def test_a_difference_under_a_tenth_of_a_penny_is_not_a_change() -> None:
    _recorder_under_test = _recorder()
    await _recorder_under_test.record(
        _LIMIT,
        fuel_price_pence_per_litre=150.0,
        dynamic_threshold_incl_vat=_THRESHOLD,
    )

    assert not _recorder_under_test.fuel_price_changed(150.0004, _THRESHOLD)
    assert _recorder_under_test.fuel_price_changed(150.001, _THRESHOLD)


@pytest.mark.parametrize("fuel_price", [None, float("nan"), -1.0, 10000.0])
def test_an_unusable_fuel_price_is_never_a_change(fuel_price: object) -> None:
    assert not _recorder().fuel_price_changed(fuel_price, _THRESHOLD)


@pytest.mark.parametrize("threshold", [None, float("nan"), 10_000_000.0])
def test_a_price_is_not_a_change_when_the_threshold_could_not_be_stored_beside_it(
    threshold: object,
) -> None:
    # record() stores NULL for both in this case, so a row for this pair would
    # carry no price: reporting a change would make the scheduler write a row
    # on every cycle.
    assert not _recorder().fuel_price_changed(150.0, threshold)


async def test_a_price_dropped_because_the_threshold_is_unstorable_is_not_remembered() -> (
    None
):
    _recorder_under_test = _recorder()
    await _recorder_under_test.record(
        _LIMIT,
        fuel_price_pence_per_litre=150.0,
        dynamic_threshold_incl_vat=10_000_000.0,
    )

    assert _recorder_under_test.fuel_price_changed(150.0, _THRESHOLD)
