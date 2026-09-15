import logging
from pathlib import Path

import pytest
from schedule.behaviour import load_threshold_extension

from config import ExtensionEntry

_VALID_PROVIDER_SOURCE = """
class FakeThresholdProvider:
    def __init__(self, config: dict) -> None:
        self.config = config

    async def get_threshold(self) -> float | None:
        return 7.5
"""


async def test_load_threshold_extension_returns_none_when_no_entry_is_configured() -> (
    None
):
    result = await load_threshold_extension(None, Path("/does-not-matter"))

    assert result is None


async def test_load_threshold_extension_loads_and_wraps_the_configured_provider(
    tmp_path: Path,
) -> None:
    (tmp_path / "fuel_price.py").write_text(_VALID_PROVIDER_SOURCE, encoding="utf-8")
    entry = ExtensionEntry(name="fuel_price", config={})

    result = await load_threshold_extension(entry, tmp_path)

    assert result is not None
    assert result.name == "fuel_price"
    assert await result.invoke("get_threshold") == 7.5


async def test_load_threshold_extension_logs_the_charging_threshold_kind_label_on_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Regression guard mirroring the LED "kind" discriminator tests
    # (.agent-docs/review.md, PR #161: "Untested discriminator/label
    # propagation in a shared wrapper") -- a reverted or mismatched kind
    # string here would pass every other test in this file silently, since
    # they only assert the happy path.
    entry = ExtensionEntry(name="does-not-exist", config={})

    with caplog.at_level(logging.ERROR):
        result = await load_threshold_extension(entry, tmp_path)

    assert result is None
    assert len(caplog.records) == 1
    assert "charging threshold extension" in caplog.records[0].message
    assert "does-not-exist" in caplog.records[0].message
