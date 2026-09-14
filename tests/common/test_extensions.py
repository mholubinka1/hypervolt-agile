import logging
from pathlib import Path

import pytest
from common.extensions import ExtensionWrapper, load_extensions

from config import ExtensionEntry

_FAKE_WIDGET_PROVIDER_SOURCE = """
class FakeWidgetProvider:
    def __init__(self, config: dict) -> None:
        self.config = config

    async def get_widget(self) -> str:
        return "a widget"
"""


def _write_extension(extensions_dir: Path, name: str, source: str) -> None:
    extensions_dir.mkdir(parents=True, exist_ok=True)
    (extensions_dir / f"{name}.py").write_text(source, encoding="utf-8")


async def test_load_extensions_loads_and_wraps_a_provider_identified_by_a_custom_marker_method(
    tmp_path: Path,
) -> None:
    _write_extension(tmp_path, "fake_widget", _FAKE_WIDGET_PROVIDER_SOURCE)
    entries = [ExtensionEntry(name="fake_widget", config={})]

    result = await load_extensions(
        entries, tmp_path, marker_method="get_widget", kind="widget provider"
    )

    assert len(result) == 1
    assert result[0].name == "fake_widget"
    widget = await result[0].invoke("get_widget")
    assert widget == "a widget"


class _FlakyThenRecoveringProvider:
    """Fails its marker method on the first N calls, then succeeds -- the
    generic shape any provider's method can take, not just LED's resolve()."""

    def __init__(self, exception: Exception, failures_before_success: int) -> None:
        self._exception = exception
        self._remaining_failures = failures_before_success

    async def get_widget(self) -> str:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise self._exception
        return "a widget"


async def test_extension_wrapper_invoke_dedups_a_repeated_identical_failure_and_logs_recovery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = _FlakyThenRecoveringProvider(
        ValueError("widget API unreachable"), failures_before_success=2
    )
    wrapper = ExtensionWrapper(
        name="fake_widget", provider=provider, kind="widget provider"
    )

    with caplog.at_level(logging.INFO):
        first = await wrapper.invoke("get_widget")
        caplog.clear()
        second = await wrapper.invoke("get_widget")

    assert first is None
    assert second is None
    assert len(caplog.records) == 0  # identical failure deduped, not re-logged

    with caplog.at_level(logging.INFO):
        third = await wrapper.invoke("get_widget")

    assert third == "a widget"
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "INFO"
    assert "fake_widget" in caplog.records[0].message
    # Proves the kind label actually flows through into the log line, not
    # just a hardcoded "Extension" -- the whole point of making it required.
    assert "widget provider" in caplog.records[0].message
