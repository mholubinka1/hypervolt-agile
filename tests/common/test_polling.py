import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from common.polling import daily_at

_LONDON = ZoneInfo("Europe/London")


def _frozen_clock(now: datetime) -> Mock:
    _clock = Mock(wraps=datetime)
    _clock.now.return_value = now
    return _clock


async def test_daily_at_sleeps_until_the_target_time_later_today() -> None:
    _now = datetime(2026, 8, 25, 10, 0, tzinfo=_LONDON)

    async def _task() -> None:
        raise asyncio.CancelledError

    with (
        patch("common.polling.datetime", _frozen_clock(_now)),
        patch("common.polling.asyncio.sleep", AsyncMock()) as _sleep,
        pytest.raises(asyncio.CancelledError),
    ):
        await daily_at(23, 0, _LONDON, _task)

    _sleep.assert_awaited_once()
    assert _sleep.await_args.args[0] == pytest.approx(13 * 3600)


async def test_daily_at_sleeps_until_tomorrow_when_the_target_time_has_already_passed() -> (
    None
):
    _now = datetime(2026, 8, 25, 23, 30, tzinfo=_LONDON)

    async def _task() -> None:
        raise asyncio.CancelledError

    with (
        patch("common.polling.datetime", _frozen_clock(_now)),
        patch("common.polling.asyncio.sleep", AsyncMock()) as _sleep,
        pytest.raises(asyncio.CancelledError),
    ):
        await daily_at(23, 0, _LONDON, _task)

    _sleep.assert_awaited_once()
    assert _sleep.await_args.args[0] == pytest.approx(23.5 * 3600)


async def test_daily_at_runs_the_task_after_sleeping() -> None:
    _calls = {"count": 0}

    async def _task() -> None:
        _calls["count"] += 1
        raise asyncio.CancelledError

    with (
        patch(
            "common.polling.datetime",
            _frozen_clock(datetime(2026, 8, 25, 10, 0, tzinfo=_LONDON)),
        ),
        patch("common.polling.asyncio.sleep", AsyncMock()),
        pytest.raises(asyncio.CancelledError),
    ):
        await daily_at(23, 0, _LONDON, _task)

    assert _calls["count"] == 1


async def test_daily_at_repeats_after_the_first_run() -> None:
    _calls = {"count": 0}

    async def _task() -> None:
        _calls["count"] += 1
        if _calls["count"] == 2:
            raise asyncio.CancelledError

    with (
        patch(
            "common.polling.datetime",
            _frozen_clock(datetime(2026, 8, 25, 10, 0, tzinfo=_LONDON)),
        ),
        patch("common.polling.asyncio.sleep", AsyncMock()) as _sleep,
        pytest.raises(asyncio.CancelledError),
    ):
        await daily_at(23, 0, _LONDON, _task)

    assert _calls["count"] == 2
    assert _sleep.await_count == 2


async def test_daily_at_does_not_die_from_an_unhandled_task_exception() -> None:
    _calls = {"count": 0}

    async def _task() -> None:
        _calls["count"] += 1
        if _calls["count"] == 1:
            raise ValueError("boom")
        raise asyncio.CancelledError

    with (
        patch(
            "common.polling.datetime",
            _frozen_clock(datetime(2026, 8, 25, 10, 0, tzinfo=_LONDON)),
        ),
        patch("common.polling.asyncio.sleep", AsyncMock()),
        pytest.raises(asyncio.CancelledError),
    ):
        await daily_at(23, 0, _LONDON, _task)

    assert _calls["count"] == 2


async def test_daily_at_supports_a_synchronous_task() -> None:
    _calls = {"count": 0}

    def _task() -> None:
        _calls["count"] += 1

    async def _sleep_then_cancel(_seconds: float) -> None:
        if _calls["count"] >= 1:
            raise asyncio.CancelledError

    with (
        patch(
            "common.polling.datetime",
            _frozen_clock(datetime(2026, 8, 25, 10, 0, tzinfo=_LONDON)),
        ),
        patch(
            "common.polling.asyncio.sleep", AsyncMock(side_effect=_sleep_then_cancel)
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await daily_at(23, 0, _LONDON, _task)

    assert _calls["count"] == 1
