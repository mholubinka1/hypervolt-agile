from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from common.extensions import ExtensionWrapper
from common.extensions import load_extensions as _load_extensions

if TYPE_CHECKING:
    from config import ExtensionEntry

# Owned here rather than inlined at each call site -- mirrors
# hypervolt.led's own load_extensions(), which likewise owns its
# marker_method ("resolve") and kind ("LED theme extension") internally so
# a caller can never pass a mismatched/reverted string. Keeping this as a
# single source also makes the kind label directly unit-testable in
# isolation (see tests/schedule/test_behaviour.py), the same way LED's is.
_MARKER_METHOD = "get_threshold"
_KIND = "charging threshold extension"


class BehaviourProvider(Protocol):
    def __init__(self, config: dict[str, Any], update_every_mins: int) -> None: ...

    async def get_threshold(self) -> float | None: ...

    # start()/stop() are optional lifecycle hooks (ADR 0005) -- deliberately
    # not declared here, same reasoning as LedThemeProvider in
    # hypervolt/led.py: a Protocol member would make them structurally
    # required. Callers check via ExtensionWrapper/load_extensions instead.


class _ThresholdValidatingProvider:
    # Wraps a BehaviourProvider so its get_threshold() result is validated
    # before the generic ExtensionWrapper's isolation/dedup logic ever sees
    # it -- mirrors hypervolt.led's _LedThemeValidatingProvider, but checks a
    # value range (finite and positive) rather than a type, since a
    # threshold provider already returns a float | None by construction.
    # Any other attribute (start, stop, ...) is delegated to the real
    # provider untouched.
    def __init__(self, provider: BehaviourProvider) -> None:
        self._provider = provider

    async def get_threshold(self) -> float | None:
        _result = await self._provider.get_threshold()
        if _result is not None and (not math.isfinite(_result) or _result <= 0):
            raise ValueError(
                f"get_threshold() returned {_result!r}, expected a finite "
                "positive value or None"
            )
        return _result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


async def load_threshold_extension(
    entry: ExtensionEntry | None, extensions_dir: Path, update_every_mins: int
) -> ExtensionWrapper | None:
    # A single optional entry, not a list -- only one charging threshold is
    # ever active (ADR 0021), unlike LED's list of extensions.
    if entry is None:
        return None
    # update_every_mins is handed to the provider as its own constructor
    # parameter, not set by the operator in the extension's own config block
    # -- the spec deliberately reuses the schedule's own cadence rather than
    # introducing a second, independently tunable interval that could drift
    # out of sync with it ("this avoids a redundant config field",
    # feature-dynamic-charging-threshold.md). entry (and entry.config) is
    # passed straight through untouched: extra_kwargs is a genuinely separate
    # channel from the operator's own config dict, so a value the operator
    # happens to write under this same key in their own config is never read,
    # let alone overwritten.
    _wrappers = await _load_extensions(
        [entry],
        extensions_dir,
        marker_method=_MARKER_METHOD,
        kind=_KIND,
        extra_kwargs={"update_every_mins": update_every_mins},
    )
    if not _wrappers:
        return None
    _wrapper = _wrappers[0]
    return ExtensionWrapper(
        name=_wrapper.name,
        provider=_ThresholdValidatingProvider(_wrapper.provider),
        kind=_KIND,
    )
