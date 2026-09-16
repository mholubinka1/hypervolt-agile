import logging
from pathlib import Path
from unittest.mock import patch

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


_FAKE_BEHAVIOUR_PROVIDER_SOURCE = """
class FakeBehaviourProvider:
    def __init__(self, config: dict) -> None:
        self.config = config

    async def get_threshold(self) -> float | None:
        return 12.5
"""


async def test_load_extensions_loads_a_behaviour_provider_implementing_only_get_threshold(
    tmp_path: Path,
) -> None:
    # Proves BehaviourProvider (issue #157) integrates with the shared loader
    # from issue #124 end-to-end, file-based -- not just an in-memory fake --
    # the same way FakeWidgetProvider above proves the loader generically.
    _write_extension(tmp_path, "fake_behaviour", _FAKE_BEHAVIOUR_PROVIDER_SOURCE)
    entries = [ExtensionEntry(name="fake_behaviour", config={})]

    result = await load_extensions(
        entries,
        tmp_path,
        marker_method="get_threshold",
        kind="charging threshold extension",
    )

    assert len(result) == 1
    assert result[0].name == "fake_behaviour"
    threshold = await result[0].invoke("get_threshold")
    assert threshold == 12.5


_FAKE_EXTRA_KWARGS_PROVIDER_SOURCE = """
class FakeExtraKwargsProvider:
    def __init__(self, config: dict, some_key: str) -> None:
        self.config = config
        self.some_key = some_key

    async def get_widget(self) -> str:
        return self.some_key
"""


async def test_load_extensions_passes_extra_kwargs_through_to_the_providers_constructor(
    tmp_path: Path,
) -> None:
    # extra_kwargs is the seam behaviour.py uses to hand the threshold
    # extension its own update_every_mins as a genuinely separate
    # constructor parameter, rather than smuggling it into entry.config --
    # proven here generically, against a fake provider, one layer below the
    # threshold-specific wiring in tests/schedule/test_behaviour.py.
    _write_extension(tmp_path, "fake_extra_kwargs", _FAKE_EXTRA_KWARGS_PROVIDER_SOURCE)
    entries = [ExtensionEntry(name="fake_extra_kwargs", config={})]

    result = await load_extensions(
        entries,
        tmp_path,
        marker_method="get_widget",
        kind="widget provider",
        extra_kwargs={"some_key": "some_value"},
    )

    assert len(result) == 1
    assert await result[0].invoke("get_widget") == "some_value"


async def test_load_extensions_skips_an_entry_whose_module_spec_cannot_be_created(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # _load_provider_class's own defensive check (importlib.util.spec_from_
    # file_location, or its .loader, returning None) has no realistic trigger
    # through real file operations -- every caller always appends ".py" to
    # the module path, and importlib resolves a loader for that suffix by
    # itself, so the branch is unreachable via a genuinely malformed or
    # missing file. Proven here by patching spec_from_file_location directly
    # rather than trying to construct a file that provokes it naturally.
    _write_extension(tmp_path, "fake_widget", _FAKE_WIDGET_PROVIDER_SOURCE)
    entries = [ExtensionEntry(name="fake_widget", config={})]

    with (
        patch("importlib.util.spec_from_file_location", return_value=None),
        caplog.at_level(logging.ERROR),
    ):
        result = await load_extensions(
            entries, tmp_path, marker_method="get_widget", kind="widget provider"
        )

    assert result == []
    assert len(caplog.records) == 1
    assert "fake_widget" in caplog.records[0].message
    assert "Could not load module spec" in caplog.records[0].message


def _widget_provider_source(widget: str) -> str:
    # get_widget() deliberately reads WIDGET_NAME back through
    # sys.modules[__name__] rather than closing over a local/module-level
    # reference directly -- a sys.modules key collision doesn't change which
    # class object gets returned from _load_provider_class (module_from_spec
    # + exec_module always builds a genuinely fresh class per load,
    # regardless of the dict key), so a test asserting only the returned
    # value from a provider that captures its own constant directly would
    # pass identically against the old, colliding key. Reading back through
    # sys.modules[__name__] is what actually breaks under a collision: the
    # second load's module silently replaces the first's under the shared
    # key, so the first provider's own sys.modules[__name__] lookup starts
    # returning the *second* module's WIDGET_NAME instead of its own.
    return f"""
import sys

WIDGET_NAME = {widget!r}


class FakeWidgetProvider:
    def __init__(self, config: dict) -> None:
        self.config = config

    async def get_widget(self) -> str:
        return sys.modules[__name__].WIDGET_NAME
"""


async def test_load_extensions_does_not_collide_two_entries_sharing_a_filename_stem_in_different_subfolders(
    tmp_path: Path,
) -> None:
    # Two different provider kinds can share one extensions_dir (ADR 0021's
    # subfolder convention, e.g. "themes/foo" vs "vehicles/foo") -- the
    # sys.modules registration key must be derived from each entry's own
    # relative name, not just the filename stem, or the second load's module
    # silently replaces the first's under a lookup the first is still relying
    # on (see PR #161 review).
    _write_extension(
        tmp_path / "kind_a", "foo", _widget_provider_source("widget from kind_a")
    )
    _write_extension(
        tmp_path / "kind_b", "foo", _widget_provider_source("widget from kind_b")
    )

    result_a = await load_extensions(
        [ExtensionEntry(name="kind_a/foo", config={})],
        tmp_path,
        marker_method="get_widget",
        kind="kind a",
    )
    result_b = await load_extensions(
        [ExtensionEntry(name="kind_b/foo", config={})],
        tmp_path,
        marker_method="get_widget",
        kind="kind b",
    )

    assert await result_a[0].invoke("get_widget") == "widget from kind_a"
    assert await result_b[0].invoke("get_widget") == "widget from kind_b"


async def test_load_extensions_does_not_collide_two_entries_whose_dotted_names_flatten_the_same(
    tmp_path: Path,
) -> None:
    # A literal "." inside a path segment must not let two genuinely
    # different entries flatten to the same dotted module key --
    # "group.a/foo" and "group/a.foo" would collapse to "group.a.foo" without
    # escaping the "." first.
    _write_extension(
        tmp_path / "group.a", "foo", _widget_provider_source("widget from group.a/foo")
    )
    _write_extension(
        tmp_path / "group", "a.foo", _widget_provider_source("widget from group/a.foo")
    )

    result_a = await load_extensions(
        [ExtensionEntry(name="group.a/foo", config={})],
        tmp_path,
        marker_method="get_widget",
        kind="kind a",
    )
    result_b = await load_extensions(
        [ExtensionEntry(name="group/a.foo", config={})],
        tmp_path,
        marker_method="get_widget",
        kind="kind b",
    )

    assert await result_a[0].invoke("get_widget") == "widget from group.a/foo"
    assert await result_b[0].invoke("get_widget") == "widget from group/a.foo"


async def test_load_extensions_does_not_collide_when_a_name_already_contains_a_percent_escape(
    tmp_path: Path,
) -> None:
    # Escaping only "." (without also escaping "%" first) would let
    # "group.a/foo" and "group%2Ea/foo" -- a name that already contains a
    # literal "%2E" -- both flatten to "group%2Ea.foo".
    _write_extension(
        tmp_path / "group.a", "foo", _widget_provider_source("widget from group.a/foo")
    )
    _write_extension(
        tmp_path / "group%2Ea",
        "foo",
        _widget_provider_source("widget from group%2Ea/foo"),
    )

    result_a = await load_extensions(
        [ExtensionEntry(name="group.a/foo", config={})],
        tmp_path,
        marker_method="get_widget",
        kind="kind a",
    )
    result_b = await load_extensions(
        [ExtensionEntry(name="group%2Ea/foo", config={})],
        tmp_path,
        marker_method="get_widget",
        kind="kind b",
    )

    assert await result_a[0].invoke("get_widget") == "widget from group.a/foo"
    assert await result_b[0].invoke("get_widget") == "widget from group%2Ea/foo"


async def test_extension_wrapper_invoke_returns_none_for_a_method_the_provider_lacks() -> (
    None
):
    # Mirrors how hypervolt.led's own ExtensionWrapper.resolve_fallback guards
    # this same absence with its own hasattr check before ever calling
    # invoke() -- but the generic wrapper must handle an absent method safely
    # on its own too, for any future caller that doesn't pre-guard.
    class _ProviderWithoutTheMethod:
        pass

    wrapper = ExtensionWrapper(
        name="fake_widget", provider=_ProviderWithoutTheMethod(), kind="widget provider"
    )

    result = await wrapper.invoke("get_widget")

    assert result is None


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
