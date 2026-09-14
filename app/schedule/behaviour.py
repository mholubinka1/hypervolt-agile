from __future__ import annotations

from typing import Any, Protocol


class BehaviourProvider(Protocol):
    def __init__(self, config: dict[str, Any]) -> None: ...

    async def get_threshold(self) -> float | None: ...

    # start()/stop() are optional lifecycle hooks (ADR 0005) -- deliberately
    # not declared here, same reasoning as LedThemeProvider in
    # hypervolt/led.py: a Protocol member would make them structurally
    # required. Callers check via ExtensionWrapper/load_extensions instead.
