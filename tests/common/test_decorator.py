import pytest
from common.decorator import retry


@pytest.mark.asyncio
async def test_retry_returns_result_on_first_success() -> None:
    calls = 0

    @retry(stop_after=3, retry_delay=0)
    async def succeeds() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await succeeds() == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_retry_recovers_after_transient_failures() -> None:
    calls = 0

    @retry(stop_after=3, retry_delay=0)
    async def fails_twice_then_succeeds() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ValueError("transient")
        return "ok"

    assert await fails_twice_then_succeeds() == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_raises_after_exhausting_attempts() -> None:
    calls = 0

    @retry(stop_after=2, retry_delay=0)
    async def always_fails() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        await always_fails()
    assert calls == 2
