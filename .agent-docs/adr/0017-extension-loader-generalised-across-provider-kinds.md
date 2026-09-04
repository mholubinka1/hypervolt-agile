# The extension loader is generalised to support multiple provider protocols, not just LedThemeProvider

Feature 16 (Volvo battery-target charging) needs the same shape of thing LED Theme Extensions
already provide — operator-registered, dynamically-loaded, self-contained modules under
`extensions/`, each isolated from the others' failures — but resolving a `VehicleStatus`
(battery percent, freshness, connection state) instead of an `LedTheme`. Building a second,
parallel loader (`vehicle_extensions/` + `--vehicle-extensions-dir` + a duplicate
`_load_provider_class`/`ExtensionWrapper`) would duplicate real logic: the dynamic
`importlib`-based module loading, the `sys.modules` registration workaround, the
`extensions_dir` path-traversal guard, and the failure-isolation/dedup-logging wrapper are all
protocol-agnostic already — only the "which method identifies a valid provider class"
check (`hasattr(cls, "resolve")`) is LED-specific.

Decision: extract the generic parts (module loading, path guard, wrapper isolation/dedup) into
a shared home, parameterised by the marker method name and the wrapper's own dispatch method,
so both `LedThemeProvider` (`resolve`) and `VehicleProvider` (`get_battery_status`) load through
the same mechanism, one `extensions/` directory, one `--extensions-dir` flag. `hypervolt/led.py`
keeps only what's LED-specific: the `LedTheme` dataclass, built-in/custom theme resolution, and
its own thin wrapper around the shared loader.

This is hard to reverse once a second provider kind depends on it — untangling two call sites
back into separate loaders later would be real work, not a config flip. It's also not obvious
from reading `led.py` alone why a battery-monitoring feature's code would live nearby, so a
future reader touching either extension kind needs this context.
