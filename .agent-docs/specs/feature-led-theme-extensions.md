# LED Theme Extensions

## Problem Statement

Calendar-based themes (built-in and custom YAML) only cover dates known in advance. An operator
who wants the charger's LEDs to react to something that doesn't follow a fixed calendar — a
football team's match day, a solar generation threshold, any external event — has no way to
express that with a static date window.

## Solution

Operators can register a Python module implementing a small `LedThemeProvider` protocol, which
decides dynamically whether a theme applies right now — by calling an external API, reading a
sensor, or any other logic the operator writes. Registered extensions sit at the top of the
existing priority stack, above custom YAML themes and built-ins, since they represent deliberate
operator behaviour. A reference implementation, `saints_fc`, ships with the app to demonstrate the
pattern against a real sports-fixtures use case.

This is the third and final LED Theme Control slice, building on both prior slices. It also
introduces a new deployment concern the first two didn't: extensions are executable operator code,
not declarative data, so they need their own isolated delivery mechanism, separate from
`/config`.

## User Stories

1. As an operator, I want to register dynamic LED theme extensions, so that the charger can
   respond to real-world events (like a football match) beyond what a calendar window can
   express.

## Implementation Decisions

**`LedThemeProvider` protocol** (`app/hypervolt/led.py`):
```python
class LedThemeProvider(Protocol):
    def __init__(self, config: dict) -> None: ...
    async def start(self) -> None: ...       # optional; default no-op
    async def resolve(self, now: datetime) -> Optional[LedTheme]: ...
```
`start()` is called once, at load time, before the scheduler's poll loop begins. An extension
needing live data (an HTTP call, a sensor read) starts its own `asyncio.create_task()` background
poll loop inside `start()`, on whatever cadence it chooses, and caches the result on itself.
`resolve(now)` must do nothing but return that cached value. **The scheduler never awaits
anything I/O-bound from an extension** — see ADR 0002. This is a hard contract, not just a
convention: the only reason a per-call timeout isn't needed anywhere in `resolve_theme` is that
`resolve()` is guaranteed cheap by construction.

**`ExtensionWrapper`** (`app/hypervolt/led.py`): wraps one loaded `LedThemeProvider` instance.
Fields: `name: str`, `_provider: LedThemeProvider`, `_last_exception: BaseException | None`.
`resolve(now)` calls `_provider.resolve(now)`, catching all exceptions:
- On failure: if `type(e) is not type(_last_exception)` or `str(e) != str(_last_exception)`, log
  a warning naming the extension and the exception, store it as `_last_exception`; otherwise
  suppress silently (already logged, nothing new to report). Either way, return `None` — the
  priority stack falls through to the next tier.
- On success: if `_last_exception is not None`, log an info-level recovery message and clear it;
  return the result.

**`load_extensions(entries, extensions_dir) -> list[ExtensionWrapper]`** (`app/hypervolt/led.py`):
for each `ExtensionEntry` in `LedConfig.extensions`, import `<extensions_dir>/<name>.py`,
locate the class implementing `LedThemeProvider`, instantiate it with the entry's `config` dict
(defaulting to `{}` when the entry omits `config:` entirely — an extension needing no
configuration should not have to declare an empty block), call `await instance.start()`, and wrap
the result in an `ExtensionWrapper`. **Any failure in this sequence** — file not found, no class
in the module implementing the protocol, `__init__` raising on bad config, or `start()` raising —
logs an error naming the extension and the underlying exception, and that extension is simply
omitted from the returned list. The app starts regardless, and every other extension, custom
theme, and built-in continues to work (ADR 0004 — LED config degrades gracefully everywhere,
including here).

**`resolve_theme(now, extensions, custom_themes)`**: the extension tier (previously always empty)
is now populated — walks `extensions` in config list order, calling each wrapper's `resolve(now)`
(cheap by contract) and returning the first non-`None` result. Falls through to custom themes,
then built-ins, exactly as already implemented in the prior two slices.

**Extension config isolation** (ADR 0003): each `ExtensionEntry`'s `config: dict` is passed to
that extension alone, at construction time. There is no shared or global configuration read from
anywhere else — every extension must be able to operate correctly from its own `config:` block in
total isolation from every other extension.

**Deployment — new `/extensions` mount** (ADR 0003): `extensions/*.py` is executable operator
code, so it cannot live inside `app/` (baked into the Docker image at build time) or share
`/config` (kept as global app configuration only). New `--extensions-dir` CLI argument on
`main.py` (optional, default `None` — required only if `led.extensions` is non-empty; if
extensions are configured but no directory was supplied, that's an extension-loading failure and
is logged and skipped per the policy above, not a startup crash). `Dockerfile`'s `CMD` passes
`--extensions-dir /extensions`; `docker-compose.yml` adds a new volume mount,
`/home/pi/.config/hypervolt-agile-extensions:/extensions`, distinct from the existing `/config`
mount.

**`app/config.py`**: new `ExtensionEntry(BaseModel)` with `name: str`, `config: dict = {}`.
`LedConfig` gains `extensions: list[ExtensionEntry] = []`.

**`extensions/saints_fc.py`** (reference implementation): documents its required `config` keys
(`api_key`, `team_id`, `poll_interval_secs`) in a module docstring, demonstrating the `start()` /
background-poll / cache pattern against a real fixtures API.

**Wire format** (already established by prior slices, unchanged): a `steady_array` effect from an
extension uses the same `{"method": "sync.apply", "params": {"effect_name": "steady_array",
"leds": [...]}}` shape as a custom YAML theme.

## Testing Decisions

No automated tests, per project convention ([[no-tests]]) — verification through execution:
- Register a minimal test extension whose `resolve()` always returns a theme, and confirm it
  takes priority over a simultaneously-matching custom theme and built-in.
- Register an extension with no `config:` key and confirm it loads with `{}` and functions.
- Point `led.extensions` at a non-existent file and confirm the app still starts, an error is
  logged naming the missing extension, and other themes still resolve correctly.
- Make a test extension's `resolve()` raise, and confirm: a warning is logged once, the warning
  is not repeated for the same exception on subsequent cycles, the rest of LED control proceeds
  normally (falls through to the next tier), and an info-level recovery message logs once
  `resolve()` starts succeeding again.
- Make a test extension's `start()` spin up a background task that never resolves quickly (e.g. a
  deliberately slow first fetch) and confirm the scheduler's poll cycle — lock control, schedule
  verification — is not delayed waiting for it.

## Out of Scope

- A registry or marketplace for sharing extensions between operators — extensions are plain files
  an operator places on disk themselves.
- Any sandboxing or resource-limiting of extension code beyond the exception isolation in
  `ExtensionWrapper` — an extension is trusted operator-authored code, same trust level as
  `config.yml` itself.
- Hot-reloading extensions without an app restart — `main.py` already restarts the whole app on
  any `config.yml` change; the same applies to extension files.

## Further Notes

Depends on both prior slices having merged — this is the final tier of an already-established
priority stack and diffing mechanism, not new architecture. The `start()` hook and the
`/extensions` mount are the two genuinely new pieces of infrastructure this slice adds; see ADR
0002 and ADR 0003 for the reasoning behind both.
