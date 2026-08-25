import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from hypervolt.led import LedTheme, load_extensions

from config import ExtensionEntry

_LONDON = ZoneInfo("Europe/London")

_VALID_EXTENSION_SOURCE = """
from datetime import datetime

from hypervolt.led import LedTheme


class FakeExtension:
    def __init__(self, config: dict) -> None:
        self.config = config

    async def resolve(self, now: datetime) -> LedTheme | None:
        return LedTheme(effect_name="fake")
"""

_START_RAISES_EXTENSION_SOURCE = """
from datetime import datetime

from hypervolt.led import LedTheme


class BrokenStartExtension:
    def __init__(self, config: dict) -> None:
        self.config = config

    async def start(self) -> None:
        raise RuntimeError("could not connect")

    async def resolve(self, now: datetime) -> LedTheme | None:
        return LedTheme(effect_name="broken")
"""

_INIT_RAISES_EXTENSION_SOURCE = """
from datetime import datetime

from hypervolt.led import LedTheme


class BrokenInitExtension:
    def __init__(self, config: dict) -> None:
        raise KeyError("api_key")

    async def resolve(self, now: datetime) -> LedTheme | None:
        return LedTheme(effect_name="broken")
"""

_START_RAISES_AFTER_OPENING_A_RESOURCE_SOURCE = """
from datetime import datetime
from pathlib import Path

from hypervolt.led import LedTheme


class BrokenStartAfterOpenExtension:
    def __init__(self, config: dict) -> None:
        self._stop_sentinel = Path(config["stop_sentinel"])

    async def start(self) -> None:
        raise RuntimeError("could not connect")

    async def stop(self) -> None:
        self._stop_sentinel.write_text("stopped")

    async def resolve(self, now: datetime) -> LedTheme | None:
        return LedTheme(effect_name="broken")
"""


def _write_extension(extensions_dir: Path, name: str, source: str) -> None:
    extensions_dir.mkdir(parents=True, exist_ok=True)
    (extensions_dir / f"{name}.py").write_text(source, encoding="utf-8")


async def test_load_extensions_loads_a_valid_entry(tmp_path: Path) -> None:
    _write_extension(tmp_path, "fake_ext", _VALID_EXTENSION_SOURCE)
    entries = [ExtensionEntry(name="fake_ext", config={"key": "value"})]

    result = await load_extensions(entries, tmp_path)

    assert len(result) == 1
    assert result[0].name == "fake_ext"
    theme = await result[0].resolve(datetime.now(tz=_LONDON))
    assert theme == LedTheme(effect_name="fake")


async def test_load_extensions_drops_entry_with_missing_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_extension(tmp_path, "fake_ext", _VALID_EXTENSION_SOURCE)
    entries = [
        ExtensionEntry(name="fake_ext"),
        ExtensionEntry(name="does-not-exist"),
    ]

    with caplog.at_level(logging.ERROR):
        result = await load_extensions(entries, tmp_path)

    assert len(result) == 1
    assert result[0].name == "fake_ext"
    assert len(caplog.records) == 1
    assert "does-not-exist" in caplog.records[0].message


async def test_load_extensions_drops_entry_with_no_valid_provider_class(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_extension(tmp_path, "empty_ext", "X = 1\n")
    _write_extension(tmp_path, "fake_ext", _VALID_EXTENSION_SOURCE)
    entries = [
        ExtensionEntry(name="empty_ext"),
        ExtensionEntry(name="fake_ext"),
    ]

    with caplog.at_level(logging.ERROR):
        result = await load_extensions(entries, tmp_path)

    assert len(result) == 1
    assert result[0].name == "fake_ext"
    assert len(caplog.records) == 1
    assert "empty_ext" in caplog.records[0].message


async def test_load_extensions_drops_entry_whose_start_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_extension(tmp_path, "broken_ext", _START_RAISES_EXTENSION_SOURCE)
    _write_extension(tmp_path, "fake_ext", _VALID_EXTENSION_SOURCE)
    entries = [
        ExtensionEntry(name="broken_ext"),
        ExtensionEntry(name="fake_ext"),
    ]

    with caplog.at_level(logging.ERROR):
        result = await load_extensions(entries, tmp_path)

    assert len(result) == 1
    assert result[0].name == "fake_ext"
    assert len(caplog.records) == 1
    assert "broken_ext" in caplog.records[0].message
    assert "RuntimeError" in caplog.records[0].message


async def test_load_extensions_drops_entry_whose_init_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_extension(tmp_path, "broken_ext", _INIT_RAISES_EXTENSION_SOURCE)
    _write_extension(tmp_path, "fake_ext", _VALID_EXTENSION_SOURCE)
    entries = [
        ExtensionEntry(name="broken_ext"),
        ExtensionEntry(name="fake_ext"),
    ]

    with caplog.at_level(logging.ERROR):
        result = await load_extensions(entries, tmp_path)

    assert len(result) == 1
    assert result[0].name == "fake_ext"
    assert len(caplog.records) == 1
    assert "broken_ext" in caplog.records[0].message
    assert "KeyError" in caplog.records[0].message


async def test_load_extensions_stops_a_partially_constructed_provider_whose_start_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # __init__ succeeding but start() raising can still leave a resource open
    # (e.g. an httpx.AsyncClient) -- the partially-constructed provider must
    # still get stop() called on it so that resource doesn't leak.
    _write_extension(
        tmp_path, "broken_ext", _START_RAISES_AFTER_OPENING_A_RESOURCE_SOURCE
    )
    _stop_sentinel = tmp_path / "stopped.marker"
    entries = [
        ExtensionEntry(name="broken_ext", config={"stop_sentinel": str(_stop_sentinel)})
    ]

    with caplog.at_level(logging.ERROR):
        result = await load_extensions(entries, tmp_path)

    assert len(result) == 0
    assert _stop_sentinel.exists()
