import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from hypervolt.led import ExtensionWrapper, LedTheme

_LONDON = ZoneInfo("Europe/London")


class _FailingProvider:
    def __init__(self, exception: Exception) -> None:
        self._exception = exception

    async def resolve(self, now: datetime) -> LedTheme | None:
        raise self._exception


class _RecoveringProvider:
    def __init__(self, exception: Exception, theme: LedTheme) -> None:
        self._exception: Exception | None = exception
        self._theme = theme

    async def resolve(self, now: datetime) -> LedTheme | None:
        if self._exception is not None:
            _exception, self._exception = self._exception, None
            raise _exception
        return self._theme


async def test_extension_wrapper_logs_a_warning_naming_the_extension_and_exception_on_first_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = _FailingProvider(ValueError("fixtures API unreachable"))
    wrapper = ExtensionWrapper(name="saints_fc", provider=provider)

    with caplog.at_level(logging.WARNING):
        result = await wrapper.resolve(datetime(2026, 8, 25, 12, 0, tzinfo=_LONDON))

    assert result is None
    assert len(caplog.records) == 1
    assert "saints_fc" in caplog.records[0].message
    assert "ValueError" in caplog.records[0].message
    assert "fixtures API unreachable" in caplog.records[0].message


async def test_extension_wrapper_suppresses_a_repeated_identical_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = _FailingProvider(ValueError("fixtures API unreachable"))
    wrapper = ExtensionWrapper(name="saints_fc", provider=provider)
    _now = datetime(2026, 8, 25, 12, 0, tzinfo=_LONDON)

    with caplog.at_level(logging.WARNING):
        await wrapper.resolve(_now)
        caplog.clear()
        result = await wrapper.resolve(_now)

    assert result is None
    assert len(caplog.records) == 0


async def test_extension_wrapper_logs_recovery_after_a_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    theme = LedTheme(effect_name="saints_fc_matchday")
    provider = _RecoveringProvider(ValueError("fixtures API unreachable"), theme)
    wrapper = ExtensionWrapper(name="saints_fc", provider=provider)
    _now = datetime(2026, 8, 25, 12, 0, tzinfo=_LONDON)

    with caplog.at_level(logging.INFO):
        await wrapper.resolve(_now)
        caplog.clear()
        result = await wrapper.resolve(_now)

    assert result == theme
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "INFO"
    assert "saints_fc" in caplog.records[0].message


class _SucceedingProvider:
    def __init__(self, theme: LedTheme | None) -> None:
        self._theme = theme

    async def resolve(self, now: datetime) -> LedTheme | None:
        return self._theme


async def test_extension_wrapper_logs_nothing_on_a_successful_call_with_no_prior_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    theme = LedTheme(effect_name="saints_fc_matchday")
    wrapper = ExtensionWrapper(name="saints_fc", provider=_SucceedingProvider(theme))

    with caplog.at_level(logging.INFO):
        result = await wrapper.resolve(datetime(2026, 8, 25, 12, 0, tzinfo=_LONDON))

    assert result == theme
    assert len(caplog.records) == 0


class _StoppableProvider:
    def __init__(self) -> None:
        self.stopped = False

    async def resolve(self, now: datetime) -> LedTheme | None:
        return None

    async def stop(self) -> None:
        self.stopped = True


async def test_extension_wrapper_stop_delegates_to_the_providers_stop() -> None:
    provider = _StoppableProvider()
    wrapper = ExtensionWrapper(name="saints_fc", provider=provider)

    await wrapper.stop()

    assert provider.stopped is True


async def test_extension_wrapper_stop_is_a_no_op_when_the_provider_has_no_stop() -> (
    None
):
    provider = _SucceedingProvider(None)
    wrapper = ExtensionWrapper(name="saints_fc", provider=provider)

    await wrapper.stop()  # must not raise
