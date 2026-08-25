# LED Theme Extensions

## Problem Statement

Custom YAML themes (slice 2) cover any date an operator can name in advance, but not events whose
timing can't be known ahead of time — a football match, a weather condition, anything that only a
live data source can answer. An operator who wants the charger LEDs to react to something
happening *right now* has no way to express that: `custom_themes` windows are fixed calendar
dates, and built-in presets are hardcoded.

## Solution

Operators can register **LED Theme Extensions** — Python modules implementing the
`LedThemeProvider` protocol — that resolve a theme dynamically from any data source of their
choosing. Extensions are opted into explicitly via `config.yml`; shipping an extension file does
not activate it. Extensions take priority over custom YAML themes and built-in presets in
`resolve_theme`'s priority stack, since they represent deliberate, live operator intent.

A reference implementation, `saints_fc`, ships with the app: it polls football-data.org for
Southampton FC fixtures and applies a red/white-striped theme on any day the team plays. It exists
both as a genuinely useful default and as a worked example for operators writing their own
extension.

This is the third and final slice of Feature 19 (LED Theme Control), building on
`feature/led-brightness-and-builtin-themes` (slice 1) and `feature/led-custom-yaml-themes` (slice
2, merged as PR #84). Brightness control, built-in presets, and custom YAML themes are already
shipped and out of scope here.

## User Stories

1. As an operator, I want to register dynamic LED theme extensions, so that the charger can
   respond to real-world events (like a football match) beyond what a calendar window can
   express.

## Implementation Decisions

### `LedThemeProvider` protocol

```python
class LedThemeProvider(Protocol):
    def __init__(self, config: dict) -> None: ...
    async def start(self) -> None: ...   # optional
    async def resolve(self, now: datetime) -> LedTheme | None: ...
    async def stop(self) -> None: ...    # optional
```

**Lifecycle** (ADR 0005, extended by this slice): `start()` is called once at app startup,
immediately after that extension's own instantiation succeeds (`load_extensions` processes
entries one at a time — see below — not as an instantiate-all-then-start-all batch). An extension
needing live data
(an HTTP poll, a sensor read) starts its own background `asyncio.create_task()` inside `start()`
on its own cadence, and caches the result on itself — `resolve(now)` does nothing but return that
cached value, so it never blocks the scheduler's poll cycle regardless of how slow the real data
source is. `stop()` is called once at app shutdown from `main.py`'s existing `finally` block
(alongside `agile_client.close()` and `coordinator.close()`); it cancels and awaits any task
`start()` created, mirroring the websocket client's `disconnect()` (`cancel()`, `await`, catch
`CancelledError`). Both hooks are optional — an extension with no live-data needs (e.g. a purely
computed theme) can omit both and just implement `resolve()`.

**Hardened 2026-08-25** (code review): `ExtensionWrapper.stop()` also isolates exceptions from the
provider's `stop()` (log a warning naming the extension and exception, don't propagate) —
`main.py`'s shutdown `finally` block calls `stop()` on every loaded extension in a plain `for`
loop, so one extension raising from `stop()` must not stop the rest from being cleaned up, or the
whole point of adding `stop()` (preventing leaked background tasks) is defeated for every
extension after the first bad one.

`resolve()` is `async def` in the protocol even though a well-behaved extension never actually
awaits inside it (per the caching pattern above) — see the `resolve_theme` change below for why
this matters.

### `resolve_theme` becomes `async def`

`resolve_theme(now, extensions, custom_themes) -> LedTheme | None` in `app/hypervolt/led.py`
currently walks two tiers (`custom_themes`, `BUILT_IN_THEMES`) via the shared `_resolve_from`
helper, synchronously — no extension tier exists yet, so nothing needs awaiting. Adding the
extension tier means the function must `await` each extension's `resolve()` in turn, so
`resolve_theme` itself becomes `async def` (**ADR 0008**). The extension tier is checked first,
ahead of `custom_themes` and `BUILT_IN_THEMES`, in config list order — the first extension
returning non-`None` wins, matching the existing `_resolve_from` "first match wins" semantics for
the other two tiers.

`ScheduleCoordinator._apply_led_state` (already `async`) adds one `await` at its existing call
site. Every existing direct test of `resolve_theme` becomes an `async def test_...` with an
`await` — mechanical, since pytest-asyncio is already configured in auto mode
(`asyncio_mode = auto`, confirmed via existing test runs).

### `ExtensionWrapper` — error isolation

Wraps a `LedThemeProvider` instance. Fields: `name: str`, `_provider: LedThemeProvider`,
`_last_exception: BaseException | None`.

`resolve(now)` calls `_provider.resolve(now)`, catching all exceptions:
- **On failure**: if `type(e) is not type(_last_exception) or str(e) != str(_last_exception)`,
  log a warning naming the extension and the exception, store it as `_last_exception`, return
  `None`. If identical to the last-seen exception, suppress (no repeated log spam) and return
  `None`.
- **On success**: if `_last_exception is not None`, log an info-level recovery message and clear
  `_last_exception`; return the result.

This isolation lives entirely inside `ExtensionWrapper.resolve()` — a broken extension's `resolve`
raising never propagates to `resolve_theme` or the scheduler, so one broken extension can never
prevent the rest of the priority stack (other extensions, custom themes, built-ins) from being
checked.

### Extension loading at startup — graceful degradation (ADR 0007)

A new `load_extensions(entries, extensions_dir) -> list[ExtensionWrapper]` in `led.py`, called
once at startup from `main.py` (matching the existing `load_custom_themes_for_config` pattern).
For each entry in `led.extensions`:
1. Import `extensions_dir / f"{entry.name}.py"`.
2. Find the class implementing `LedThemeProvider` in that module.
3. Instantiate it with `entry.config` (defaulting to `{}` if the entry omits `config:`).
4. Call `await instance.start()` if the extension defines it.
5. Wrap in `ExtensionWrapper` and append to the result.

**Hardened 2026-08-25 round 2** (code review): if `__init__` succeeds but `start()` raises, the
partially-constructed instance may still hold an open resource (e.g. `saints_fc`'s
`httpx.AsyncClient`, opened in `__init__`). `load_extensions` now calls `stop()` on it via a
throwaway `ExtensionWrapper` before discarding it, reusing the same isolated stop-path a
fully-loaded extension gets rather than leaking the resource.

**Corrected from `FEATURES.md`'s original text** (agreed in this session's grill, aligning with
ADR 0007's later correction from PR #81's Copilot review): any failure in this sequence — file
missing, no valid provider class found, `__init__` or `start()` raising — is logged (naming the
extension and the exception) and that entry is **skipped**, not fatal. `FEATURES.md` still says
"fail loudly... the app must not start with a broken extension registration"; this spec supersedes
that text for the reasons ADR 0007 already gives: LED theming is a cosmetic layer that must never
be able to take down charging, and a broken extension is exactly the class of external-resource
failure ADR 0007 already carves out (the same treatment a broken `led_effects/*.yaml` file already
gets in slice 2).

### `--extensions-dir` CLI argument and Docker mount (ADR 0006)

`main.py` gains `--extensions-dir` as an **optional** argument (unlike `--config-file`, which is
required). It's only needed when `led.enabled` is true and `led.extensions` is non-empty; if
extensions are configured but the flag was omitted, fail at startup with a clear error naming the
missing flag (config values are fail-fast per ADR 0007's boundary — this is a
config-authoring/deployment mistake, not an external-resource failure). If `led.extensions` is
empty, or `led.enabled` is `false`, the flag is simply unused and extensions are never loaded or
started — **hardened 2026-08-25** (code review): the first implementation gated loading only on
`led.extensions` being non-empty, so `enabled: false` still started every extension's background
polling (burning e.g. `saints_fc`'s API quota) even though the result was never consumed by
`_apply_led_state`. This fail-fast path also now closes `agile_client` before `sys.exit(1)`,
matching the existing pattern used by the postcode-validation check just above it in `main.py`.

Per ADR 0006, this is a directory separate from `--config-file`'s directory (which is where
`led_effects/` lives) — extensions are executable code, not data, and get their own mount so
`/config` stays app configuration only. The Docker image's `CMD` is updated to always pass
`--extensions-dir /extensions`, and `docker-compose.yml` gains a new volume line —
`/home/pi/.config/hypervolt-agile-extensions:/extensions` (matching the existing
`/home/pi/.config/hypervolt-agile:/config` and `/home/pi/.log/hypervolt-agile:/logs` host-path
convention) — documented as optional/empty by default, matching the existing `led_effects` mount
note added for slice 2 in `README.md`.

### `saints_fc` reference implementation

Ships as `extensions/saints_fc.py`. Polls
[football-data.org](https://www.football-data.org/)'s `/v4/teams/{id}/matches` endpoint for
Southampton FC (`team_id: 340`, matching `FEATURES.md`'s existing example) via `httpx` (already a
project dependency), using the existing `common.decorator.retry` helper for transient failures.
`config:` keys: `api_key` (required, football-data.org API token — lives directly in `config.yml`
per this session's grill, not an environment variable), `team_id` (defaults to `340`),
`poll_interval_secs` (defaults to `300`).

`start()` launches a background task that polls the fixtures endpoint on `poll_interval_secs` and
caches whether Southampton FC have a match scheduled **today** (any status — not restricted to
"currently in progress"), in the charger's local timezone. `resolve(now)` returns a
`LedTheme(effect_name="saints_fc_matchday", leds=<red/white stripes>)` if the cached flag is
`True` for `now`'s date, else `None`. The `leds` array alternates red and white across the 51 LEDs
(exact stripe width is an implementation detail, not a behavioural contract — any visually
alternating pattern satisfies the acceptance criteria). `stop()` cancels the background polling
task.

**Poll-failure handling lives in the background task, not in `resolve()`** — per ADR 0005,
`resolve()` never does I/O, so an HTTP error can only ever occur inside the background loop
`start()` created, where `ExtensionWrapper` (which only wraps `resolve()`) can't see it. Each poll
iteration catches its own exceptions: on failure, log a warning naming the exception and **keep
serving the last successfully-cached value** rather than clearing it — a transient API blip must
not flip a real match-day theme off, mirroring `FEATURES.md` Feature 16's Volvo-integration
precedent ("5xx/network — log warning; skip cycle; resume next interval"). No suppress-on-repeat
logic is needed here (unlike `ExtensionWrapper`'s per-cycle `resolve()` isolation) — at the default
`poll_interval_secs: 300`, a warning every 5 minutes during a real outage is not log spam.

Wire-level, this behaves exactly like a custom YAML theme: `effect_name` is the semantic identity
(`"saints_fc_matchday"`), the wire value sent is `"steady_array"` with the `leds` array — no
changes needed to `apply_led_state` (already generic over any `LedTheme` with a non-`None` `leds`
field, since slice 2).

**Hardened 2026-08-25** (code review): the first implementation cached a bare `bool`
(`_has_match_today`) rather than the date it was computed for, so `resolve(now)` never actually
checked `now` against it — a stale match-day theme could linger briefly past midnight until the
next poll corrected it. Fixed by caching `_match_date: date | None` instead, compared against
`now`'s date on every `resolve()` call. Also, the initial implementation omitted the
`common.decorator.retry` usage this section already committed to — added as `@retry()` on the
HTTP-fetching call, matching `octopus/client.py`'s and `octopus/postcode.py`'s existing usage.

### `FEATURES.md` correction

The stale "fail loudly" text in Feature 19's "Files Affected" section is corrected in this repo's
local `FEATURES.md` to match the graceful-degradation decision above (a documentation-only change,
not part of this PR's diff, since `FEATURES.md` is gitignored/local-only per prior session
convention).

## Testing Decisions

Following the seam-selection precedent from slice 2 (prefer the highest seam, mock only real
boundaries):

- **`ExtensionWrapper`**: pure-logic tests — construct a fake `LedThemeProvider` (a small test
  double whose `resolve()` is scripted to raise/succeed on demand) and assert suppress-on-repeat
  and recovery-logging behaviour directly. No mocking framework needed; this is exactly the kind
  of behaviour a hand-written fake expresses more clearly than a `Mock`.
- **`load_extensions`**: integration-style tests against real files in `tmp_path`, mirroring
  `test_led_load_custom_themes.py`'s pattern exactly — write a real `.py` file implementing
  `LedThemeProvider` to a temp `extensions_dir`, load it, assert the wrapper resolves correctly.
  Covers: valid extension loads; missing file is logged and skipped; a file with no valid
  provider class is logged and skipped; `__init__`/`start()` raising is logged and skipped; a
  `caplog` assertion on the log message content (per the precedent set fixing a Copilot finding on
  the equivalent `load_custom_themes` test in slice 2).
- **`resolve_theme`**: existing tests become `async def`; new tests cover extension-tier priority
  over custom themes and built-ins, and first-extension-wins ordering — same shape as the
  existing custom-vs-built-in priority tests.
- **`saints_fc`**: mock only the real boundary — `httpx.MockTransport` wired into a real
  `httpx.AsyncClient`, so the actual request-building and response-parsing code runs for real.
  Covers: a match-today response produces the matchday theme; no-match response produces `None`;
  a poll failure logs a warning and leaves the previously-cached value unchanged (tested by
  driving the background poll function directly rather than waiting on a real
  `poll_interval_secs` sleep — see Further Notes). No live network calls in tests.
- **`main.py`'s `--extensions-dir` wiring**: verification is through execution, consistent with
  `main.py`'s existing untested procedural-wiring code (no unit tests currently exist for
  `main.py` itself).

## Out of Scope

- Any other extension beyond `saints_fc` (e.g. weather-based themes) — the protocol supports them,
  but none are being written now.
- Retrying or backing off football-data.org rate limits beyond what `common.decorator.retry`
  already provides — not a stated requirement for this slice.
- Changing `apply_led_state`, `charger.py`, or the wire protocol — slice 2 already made these
  generic over any `LedTheme`.
- Multi-team support in `saints_fc` — hardcoded to Southampton FC's fixtures, configurable only by
  `team_id` if an operator wants a different team, not a list of teams.

## Further Notes

- ADR 0005 (extended this session), 0006, 0007, and the new ADR 0008 all directly govern this
  slice's design — see `.agent-docs/adr/`.
- `FEATURES.md`'s Feature 19 "Development" section predates ADR 0007's correction; this spec is
  the authoritative source for extension load-failure behaviour going forward.
- `saints_fc`'s background task should factor "poll once, update cache, handle one failure" into
  its own method (`_poll_once`), separate from the loop that repeats it — this is what makes a
  poll failure directly testable (call `_poll_once` directly, assert on the cache) without a test
  needing to wait on or mock `asyncio.sleep`. `start()` should drive that loop via the existing
  `common.polling.every(poll_interval_secs, self._poll_once)` helper (`app/common/polling.py`) —
  the same drift-corrected, exception-catching-and-logging loop `main.py`'s own outer scheduler
  already runs on — via `asyncio.create_task(every(...))`, rather than hand-rolling a new
  `while True: ...; await asyncio.sleep(...)`. `_poll_once` still needs its own internal
  try/except so a failure logs with the extension's own context (naming `saints_fc` and the
  exception) rather than relying solely on `every`'s generic "Unhandled exception in scheduled
  task" log line.
