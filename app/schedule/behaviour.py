from __future__ import annotations

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
    def __init__(self, config: dict[str, Any]) -> None: ...

    async def get_threshold(self) -> float | None: ...

    # start()/stop() are optional lifecycle hooks (ADR 0005) -- deliberately
    # not declared here, same reasoning as LedThemeProvider in
    # hypervolt/led.py: a Protocol member would make them structurally
    # required. Callers check via ExtensionWrapper/load_extensions instead.


async def load_threshold_extension(
    entry: ExtensionEntry | None, extensions_dir: Path, update_every_mins: int
) -> ExtensionWrapper | None:
    # A single optional entry, not a list -- only one charging threshold is
    # ever active (ADR 0021), unlike LED's list of extensions.
    if entry is None:
        return None
    # update_every_mins is injected here, not set by the operator in the
    # extension's own config block -- the spec deliberately reuses the
    # schedule's own cadence rather than introducing a second, independently
    # tunable interval that could drift out of sync with it ("this avoids a
    # redundant config field", feature-dynamic-charging-threshold.md). Any
    # operator-supplied value under this key in the extension's config is
    # overridden, since that field isn't meant to be operator-configurable.
    _entry = entry.model_copy(
        update={"config": {**entry.config, "update_every_mins": update_every_mins}}
    )
    _wrappers = await _load_extensions(
        [_entry], extensions_dir, marker_method=_MARKER_METHOD, kind=_KIND
    )
    return _wrappers[0] if _wrappers else None
