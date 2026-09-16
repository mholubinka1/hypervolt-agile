from __future__ import annotations

import importlib.util
import inspect
import logging.config
import sys
from collections.abc import Sequence
from logging import Logger, getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common.constants import APP_NAME
from common.logging import config

if TYPE_CHECKING:
    from config import ExtensionEntry

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


class ExtensionWrapper:
    def __init__(self, name: str, provider: Any, kind: str) -> None:
        self.name = name
        # Public: a thin per-protocol wrapper (e.g. hypervolt.led's own
        # ExtensionWrapper) needs the constructed provider back to build its
        # own typed adapter around it, since the loader's public surface is
        # deliberately just load_extensions() + ExtensionWrapper (no method
        # to re-fetch a provider by name after loading).
        self.provider = provider
        # Required, not defaulted -- once more than one provider kind shares
        # this wrapper, a log line that doesn't name which kind produced it
        # is ambiguous the moment two kinds can share an extension name or a
        # method name (see .agent-docs/review.md's "Ambiguous log line across
        # multiplexed code paths" entry). Each caller (e.g. "LED theme
        # extension") supplies its own label rather than a generic default.
        self._kind = kind
        # Keyed by the invoked method name -- distinct provider methods are
        # independent code paths (e.g. LED's resolve() and resolve_fallback(),
        # a live-API call that can fail every cycle alongside a cached call
        # that keeps succeeding), so each dedups its own repeated warning and
        # clears its own record without another method's success spuriously
        # logging "recovered".
        self._last_exception: dict[str, Exception] = {}

    async def invoke(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        _method = getattr(self.provider, method_name, None)
        if _method is None:
            return None
        _previous = self._last_exception.get(method_name)
        try:
            _result = await _method(*args, **kwargs)
        except Exception as e:
            if type(e) is not type(_previous) or str(e) != str(_previous):
                logger.warning(
                    f"{self._kind} {self.name!r} {method_name}() failed: "
                    f"{type(e).__name__}: {e}."
                )
            self._last_exception[method_name] = e
            return None
        if _previous is not None:
            logger.info(f"{self._kind} {self.name!r} {method_name}() recovered.")
            del self._last_exception[method_name]
        return _result

    async def stop(self) -> None:
        if not hasattr(self.provider, "stop"):
            return
        try:
            await self.provider.stop()
        except Exception as e:
            logger.warning(
                f"{self._kind} {self.name!r} failed to stop cleanly: "
                f"{type(e).__name__}: {e}."
            )


def _load_provider_class(
    module_path: Path, marker_method: str, module_key: str
) -> type:
    _spec = importlib.util.spec_from_file_location(
        f"_hypervolt_extension.{module_key}", module_path
    )
    if _spec is None or _spec.loader is None:
        raise ImportError(f"Could not load module spec for {module_path}.")
    _module = importlib.util.module_from_spec(_spec)
    # module_from_spec() alone does not register the module in sys.modules --
    # unlike a normal import, so anything the module's own top-level code
    # relies on sys.modules for (e.g. @dataclass, via dataclasses._is_type,
    # looks up sys.modules[cls.__module__] directly with no default) would
    # otherwise crash during exec_module below. Matches importlib's own
    # documented recipe for loading a module from a file path. The name is
    # namespaced under "_hypervolt_extension." so an extension file that
    # happens to share a name with a real module (e.g. "config.py") can
    # never clobber -- or be clobbered by -- that module's sys.modules entry.
    # module_key is the entry's own relative name (dots for path separators),
    # not just module_path.stem -- since this loader now serves more than one
    # provider kind against the same extensions_dir, two different kinds'
    # entries can share a filename stem while differing in subfolder (e.g.
    # "themes/foo" vs "vehicles/foo", ADR 0021's subfolder convention) --
    # .stem alone would collide those into the same sys.modules key even
    # though they're different files, letting the second load's module
    # silently replace the first's under a lookup the first is still relying
    # on. Keying on the full relative name instead means two different files
    # can never collide, and the same entry loaded twice (genuinely the same
    # file) safely just re-registers itself.
    sys.modules[_spec.name] = _module
    try:
        _spec.loader.exec_module(_module)
        _candidates = [
            _cls
            for _, _cls in inspect.getmembers(_module, inspect.isclass)
            if _cls.__module__ == _module.__name__ and hasattr(_cls, marker_method)
        ]
        if len(_candidates) != 1:
            raise ValueError(
                f"{module_path}: expected exactly one class implementing "
                f"the {marker_method!r} provider protocol, found {len(_candidates)}."
            )
    except Exception:
        del sys.modules[_spec.name]
        raise
    return _candidates[0]


async def load_extensions(
    entries: Sequence[ExtensionEntry],
    extensions_dir: Path,
    marker_method: str,
    kind: str,
    extra_kwargs: dict[str, Any] | None = None,
) -> list[ExtensionWrapper]:
    _loaded: list[ExtensionWrapper] = []
    _extensions_dir = extensions_dir.resolve()
    for entry in entries:
        _provider: Any | None = None
        try:
            _module_path = (extensions_dir / f"{entry.name}.py").resolve()
            if not _module_path.is_relative_to(_extensions_dir):
                raise ValueError(
                    f"{entry.name!r} resolves outside extensions_dir {extensions_dir}."
                )
            # Escape a literal "." within a path segment before joining
            # segments with "." -- otherwise "a.b/c" and "a/b.c" would both
            # flatten to the same "a.b.c" key, reintroducing the exact
            # collision this parameter exists to prevent, just via "." rather
            # than a shared filename stem. "%" must be escaped first, not
            # just "." -- otherwise "group.a/foo" and "group%2Ea/foo" (a
            # literal "%2E" already in the name) would both produce
            # "group%2Ea.foo", the same collision one level down. Escaping
            # "%" before "." makes every encoded sequence unambiguous, the
            # standard percent-encoding ordering.
            _module_key = ".".join(
                _segment.replace("%", "%25").replace(".", "%2E")
                for _segment in entry.name.split("/")
            )
            _provider_class = _load_provider_class(
                _module_path, marker_method, _module_key
            )
            _provider = _provider_class(entry.config, **(extra_kwargs or {}))
            if hasattr(_provider, "start"):
                await _provider.start()
        except Exception as e:
            logger.error(
                f"Failed to load {kind} {entry.name!r}: {type(e).__name__}: {e}."
            )
            # __init__ succeeding but start() raising can still leave a
            # resource open (e.g. an httpx.AsyncClient) -- best-effort clean
            # it up via the same isolated stop() path a fully-loaded
            # extension gets, so a failed load never leaks.
            if _provider is not None:
                await ExtensionWrapper(
                    name=entry.name, provider=_provider, kind=kind
                ).stop()
            continue
        _loaded.append(ExtensionWrapper(name=entry.name, provider=_provider, kind=kind))
    return _loaded
