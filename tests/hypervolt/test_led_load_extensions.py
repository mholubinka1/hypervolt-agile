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

_USES_DATACLASS_WITH_POSTPONED_ANNOTATIONS_SOURCE = """
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hypervolt.led import LedTheme


@dataclass
class _Config:
    api_key: str


class DataclassExtension:
    def __init__(self, config: dict) -> None:
        self._config = _Config(api_key=config["api_key"])

    async def resolve(self, now: datetime) -> LedTheme | None:
        return LedTheme(effect_name="dataclass_extension")
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


async def test_load_extensions_loads_an_extension_using_a_dataclass_with_postponed_annotations(
    tmp_path: Path,
) -> None:
    # importlib.util.module_from_spec()/exec_module() alone does NOT register
    # the module in sys.modules, unlike a normal import -- @dataclass (via
    # dataclasses._is_type) looks up sys.modules[cls.__module__] directly
    # with no default, so a perfectly valid extension using `from __future__
    # import annotations` combined with @dataclass would otherwise crash
    # with an AttributeError on 'NoneType' during its own module exec,
    # before load_extensions' own validation even runs.
    _write_extension(
        tmp_path,
        "dataclass_ext",
        _USES_DATACLASS_WITH_POSTPONED_ANNOTATIONS_SOURCE,
    )
    entries = [ExtensionEntry(name="dataclass_ext", config={"api_key": "test-key"})]

    result = await load_extensions(entries, tmp_path)

    assert len(result) == 1
    assert result[0].name == "dataclass_ext"


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


async def test_load_extensions_rejects_a_name_that_escapes_extensions_dir_via_traversal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A malicious or mistyped `name` (e.g. "../../etc/passwd") must not be
    # able to import a Python file from outside extensions_dir -- entry.name
    # is interpolated directly into the loaded path.
    _extensions_dir = tmp_path / "extensions"
    _extensions_dir.mkdir()
    _outside_file = tmp_path / "outside.py"
    _outside_file.write_text(_VALID_EXTENSION_SOURCE, encoding="utf-8")
    _write_extension(_extensions_dir, "fake_ext", _VALID_EXTENSION_SOURCE)
    entries = [
        ExtensionEntry(name="../outside"),
        ExtensionEntry(name="fake_ext"),
    ]

    with caplog.at_level(logging.ERROR):
        result = await load_extensions(entries, _extensions_dir)

    assert len(result) == 1
    assert result[0].name == "fake_ext"
    assert len(caplog.records) == 1
    assert "../outside" in caplog.records[0].message


async def test_load_extensions_rejects_an_absolute_path_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _extensions_dir = tmp_path / "extensions"
    _extensions_dir.mkdir()
    _outside_file = tmp_path / "outside.py"
    _outside_file.write_text(_VALID_EXTENSION_SOURCE, encoding="utf-8")
    _write_extension(_extensions_dir, "fake_ext", _VALID_EXTENSION_SOURCE)
    # An absolute path as `name` would make Path.__truediv__ discard
    # extensions_dir entirely and resolve to the absolute path directly.
    entries = [
        ExtensionEntry(name=str(tmp_path / "outside")),
        ExtensionEntry(name="fake_ext"),
    ]

    with caplog.at_level(logging.ERROR):
        result = await load_extensions(entries, _extensions_dir)

    assert len(result) == 1
    assert result[0].name == "fake_ext"
    assert len(caplog.records) == 1
    assert repr(str(tmp_path / "outside")) in caplog.records[0].message


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
