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
    entry: ExtensionEntry | None, extensions_dir: Path
) -> ExtensionWrapper | None:
    # A single optional entry, not a list -- only one charging threshold is
    # ever active (ADR 0021), unlike LED's list of extensions.
    if entry is None:
        return None
    _wrappers = await _load_extensions(
        [entry], extensions_dir, marker_method=_MARKER_METHOD, kind=_KIND
    )
    return _wrappers[0] if _wrappers else None
