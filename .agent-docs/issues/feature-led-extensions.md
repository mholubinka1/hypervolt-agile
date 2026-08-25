# Issues: feature-led-extensions

> Work complete — PR ready to merge.

## Add the LED theme extension framework — [#86](https://github.com/mholubinka1/hypervolt-agile/issues/86)

**Blocked by**: None

**User stories**: 1

### What to build

`resolve_theme` gains a third, highest-priority tier: registered extensions. `LedThemeProvider`
becomes a real protocol (`__init__`, optional `start`, `resolve`, optional `stop`) instead of the
placeholder `Sequence[object]` `resolve_theme` currently accepts. `ExtensionWrapper` isolates a
misbehaving extension's `resolve()` from the rest of the priority stack, with suppress-on-repeat
and recovery logging. `load_extensions` loads registered extensions from a new `--extensions-dir`
CLI argument (optional, required only when `led.extensions` is non-empty) at startup, applying
the same log-and-skip graceful degradation already used for broken `led_effects/*.yaml` files —
per ADR 0007, a broken extension is never fatal. `resolve_theme` itself becomes `async def` to
honestly await each extension's `resolve()` (ADR 0008). Wired into `ScheduleCoordinator` and
`main.py`'s startup/shutdown, plus the Docker image's `CMD` and `docker-compose.yml`'s new
`/extensions` mount (ADR 0006).

### Acceptance criteria

- [x] Given a registered extension returns a theme for the current datetime, and a custom theme
      or built-in preset also matches, when the scheduler runs, then the extension's theme is
      applied (extensions win priority)
- [x] Given multiple registered extensions, when more than one would match, then the first in
      config list order wins (mirrors existing custom-theme/built-in "first match wins"
      semantics)
- [x] Given no extension matches, when the scheduler runs, then custom themes and built-ins are
      resolved exactly as before (no regression to slices 1/2 behaviour)
- [x] Given `saints_fc` (or any extension) is listed in `led.extensions` and its file is missing,
      when the app starts, then an error is logged naming the missing extension, the app starts
      successfully, and every other configured extension/theme/built-in still resolves correctly
      (corrected from `FEATURES.md`'s original fail-loud text — see ADR 0007)
- [x] Given a registered extension's `resolve()` raises, when the scheduler runs, then a warning
      is logged naming the extension and the exception, the next priority tier is checked, and
      the rest of LED control proceeds normally
- [x] Given a registered extension's `resolve()` raises the same exception repeatedly, when the
      scheduler runs on subsequent cycles, then the warning is not repeated
- [x] Given a registered extension has been failing with a suppressed error, when `resolve()`
      succeeds on a subsequent cycle, then an info message is logged indicating recovery
- [x] Given an extension is registered with no `config:` field, when the app starts, then it is
      instantiated with an empty dict `{}`
- [x] Given `led.extensions` is non-empty but `--extensions-dir` was not passed, when the app
      starts, then it fails with a clear error naming the missing flag
- [x] Given `led.extensions` is empty (or `led:` is absent), when the app starts, then
      `--extensions-dir` is not required
- [x] An extension's `start()` is awaited once at load time; its `stop()` is awaited once at app
      shutdown (from `main.py`'s existing `finally` block), cancelling any task `start()` created

---

## Ship the saints_fc reference extension — [#87](https://github.com/mholubinka1/hypervolt-agile/issues/87)

**Blocked by**: [#86](https://github.com/mholubinka1/hypervolt-agile/issues/86)

**User stories**: 1

### What to build

`extensions/saints_fc.py` — a real `LedThemeProvider` polling football-data.org's fixtures API
for Southampton FC (`team_id: 340`) on `poll_interval_secs` (default 300s), applying a
red/white-striped `LedTheme` (`effect_name="saints_fc_matchday"`) on any day with a scheduled
match. `config:` keys: `api_key` (required), `team_id` (default 340), `poll_interval_secs`
(default 300). Polling and its own failure handling live in a background task started from
`start()` (per ADR 0005) — `resolve()` only ever reads the cached result, never does I/O. A poll
failure logs a warning and keeps serving the last successfully-cached value rather than clearing
it. `config.yml.template` documents the `led.extensions` entry.

### Acceptance criteria

- [x] Given Southampton FC have a match scheduled today, when `resolve(now)` is called, then a
      `LedTheme` with `effect_name="saints_fc_matchday"` and an alternating red/white 51-element
      `leds` array is returned
- [x] Given no match is scheduled today, when `resolve(now)` is called, then `None` is returned
- [x] Given a poll of football-data.org fails, when the poll runs, then a warning is logged and
      the previously-cached match-day value is left unchanged (not cleared)
- [x] Given `resolve(now)` is called, then it performs no I/O itself — the cached value from the
      most recent background poll is read synchronously-in-effect
- [x] `config.yml.template` documents a `saints_fc` entry under `led.extensions` with all three
      config keys and their defaults

---
