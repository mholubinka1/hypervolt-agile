# LED Theme Display Behaviour

## Problem Statement

Three gaps in how the charger's LEDs behave once a theme is involved:

1. **Every themed window is gated the same way, with no choice.** A resolved custom or
   built-in theme lights the LEDs whenever a car is plugged in (ADR 0010), whether or not it
   is charging. The operator wants per-theme control: a seasonal strip should be able to run
   for its whole window regardless of charge or plug state, while another theme should only
   appear while the car is actually drawing power.

2. **Saints FC is all-or-nothing and cosmetic.** The `saints_fc` extension lights the
   charger for the *entire* local calendar date of a Southampton fixture, only when a car is
   plugged in, and with a placeholder alternating red/white pattern rather than the tuned
   club-strip colour map now shipped at `themes/saints_fc.yaml`. The operator wants the real
   strip, focused on the match — on from shortly before kick-off until a few hours after,
   visible on an empty charge point — while the rest of the day still behaves sensibly (any
   other configured theme wins outside that window; a bare charger still shows the strip
   while charging).

3. **Brightness is a half-measure.** `led.brightness` dims everything, and a charging
   charger with no theme still shows plain white at that brightness. The operator wants it
   binary: a theme is shown at full brightness, or the LEDs are off.

## Solution

### Binary brightness

A displaying theme is always at **full brightness (1.0)**. When nothing is displaying — no
theme resolves, or a charging-gated theme is not charging — the LEDs are **fully off
(0.0)**. The `led.brightness` config field is **removed**, and the old
"plain white while charging with no theme" state goes with it: no theme means dark, even
mid-charge. A `config.yml` still carrying `led.brightness` (or any unknown key under `led:`)
now **fails at load** rather than being silently ignored.

### The display gate

A resolved LED Theme carries a **display gate**:

- **Always-on** — light the charger for the theme's whole active window regardless of charge
  *or plug* state. An empty, unplugged charge point lights up.
- **Charging-gated** — light the charger only while a car is actively charging; otherwise
  off.

**Custom and built-in themes** gain an `always_on: bool` config field, **default `false`**
(charging-gated). `true` selects always-on for the theme's whole `start`–`end` window.
Plug state (`car_plugged`) is no longer consulted for LED display at all — this supersedes
ADR 0010's "must be plugged in" floor.

### Saints FC — two modes on a match day

On a day Southampton play, the extension has a split personality:

- **Kick-off − 30 minutes → kick-off + 3 hours** (union of these spans for a double-header):
  the tuned club strip, **always-on**, and it **outranks everything** — a custom or built-in
  theme covering the same date does not show during this window. The whole span behaves
  identically; there is no weaker "lead-in" sub-window.
- **The rest of a match day**: the club strip is a **charging-gated fallback that sits
  *below* custom and built-in themes**. Any other configured theme wins and shows per its own
  `always_on`; only when nothing else resolves does the Saints strip show, and then only
  while the car is charging.
- **Kick-off time still unknown** for that date: the date contributes **no** match window —
  only the charging-gated fallback that day. Kick-off times are polled from TheSportsDB
  hourly by default, so an unknown kick-off is a rare edge, not the norm.
- **Not a match day**: the extension contributes nothing.

## User Stories

1. As an operator, I want to mark a custom or built-in theme `always_on: true`, so that it
   lights the charger for its whole window whether or not the car is charging or even
   plugged in.
2. As an operator, I want `always_on` to default to `false` when I omit it, so that a theme
   I add without thinking about the flag only runs while the car is charging rather than
   burning the LEDs on an idle charger indefinitely.
3. As an operator, I want a malformed `always_on` value — or a typo'd key — to fail at
   config load, like every other `config.yml` mistake.
4. As an operator upgrading from a version with `led.brightness`, I want the app to tell me
   the field is gone rather than silently ignoring my setting, so I know to remove it.
5. As an operator, I want the LEDs simply off whenever no theme is showing — no dim white
   while charging — so the charger's resting state is dark.
6. As an operator, I want the Saints strip to appear from shortly before kick-off until a
   few hours after, so the charger marks the match while it is on rather than all day.
7. As an operator, I want that strip visible with no car plugged in, so it works as a
   match-day statement regardless of charging.
8. As an operator, I want the strip to use the exact colour map shipped at
   `themes/saints_fc.yaml`, not a placeholder pattern.
9. As an operator, I want a New Year's Eve (or any) custom theme to still show on a fixture
   date *outside* the kick-off window, honouring its own `always_on`, so a match doesn't
   blank my seasonal theme for the whole day.
10. As an operator, I want a bare charger with no other theme configured to still show the
    Saints strip while charging outside the kick-off window, so the club colours aren't
    entirely absent on a match day.
11. As an operator, I want a fixture whose kick-off time is still `TBD` to *not* seize the
    charger for the whole day — just contribute the charging-gated fallback — because an
    hourly poll will firm the time up well before kick-off.
12. As an operator, I want two Southampton fixtures on one date to light the strip across
    both match windows.
13. As an operator, I want kick-off times discovered automatically and kept current, so I
    never configure a fixture by hand.
14. As a maintainer, I want the fixture-date collection to stop growing without bound in a
    long-running process.

## Implementation Decisions

### Binary brightness; `led.brightness` removed

- `LedConfig` loses the `brightness` field (keeps `enabled`) and gains
  `model_config = {"extra": "forbid"}`, so a leftover `brightness` — or any unknown key
  under `led:` — raises `ValidationError` at load.
- `ScheduleCoordinator._apply_led_state` collapses to this shape (which encodes the gate
  precisely):

  ```text
  if led is None or not led.enabled: return
  if state.is_charging is None: return
  target = await resolve_theme(now, extensions, custom_themes, built_in_themes)
  if target is not None and (target.always_on or state.is_charging):
      await apply_led_state(1.0, target.effect_name, leds=target.leds); return
  await apply_led_state(0.0, None)
  ```

  `car_plugged` and any brightness config are no longer read. The former
  "charging, no theme → plain white at brightness" branch is deleted.

### Display gate carried on `LedTheme`

- `LedTheme` (frozen dataclass, `hypervolt/led.py`) gains `always_on: bool = False`.
- `resolve_theme`'s defensive-copy step preserves `always_on` alongside `effect_name` and
  the deep-copied `leds`.

### `always_on` config field

- `_WindowedLedTheme` (base of `CustomLedTheme` / `BuiltInLedTheme`) gains
  `always_on: bool = False` — an ordinary pydantic field, fail-fast at load (ADR 0007) — and
  `model_config = {"extra": "forbid"}`, so a misspelled `always_on` / `start` / `end` on a
  theme entry raises.
- `load_custom_themes` / `load_built_in_themes` pass `entry.always_on` into the `LedTheme`
  they build.

### Priority resolution — a fallback pass

`resolve_theme` runs two passes:

1. **Primary** (order unchanged, out of scope to alter): extensions (config list order) →
   custom themes (config list order) → built-in themes; first match wins.
2. **Fallback** (only if the primary pass produced nothing): each extension's optional
   `resolve_fallback(now) -> LedTheme | None`, in list order; first match wins.

Then the existing defensive copy runs on whichever pass produced the match.

- `LedThemeProvider` gains an **optional** `async def resolve_fallback(self, now) -> LedTheme | None`
  hook, declared the same way as `start` / `stop` — a comment, not a Protocol member, so it
  is not structurally required — and reached via `hasattr`. Only the Saints extension
  implements it.
- `ExtensionWrapper` gains a `resolve_fallback` method mirroring its `resolve`: the same
  `try` / `except` isolation, the same `_last_exception` dedup of repeated warning lines,
  the same "raise `TypeError` if the return is neither `None` nor an `LedTheme`" guard, and
  the same "recovered" info log. An extension without the hook makes `resolve_fallback`
  return `None`.

This is what lets the Saints strip sit *below* custom/built-in themes outside the match
window while `resolve()` keeps it *above* them inside the window.

### Saints FC extension

- **Match window**: `kick-off − 30 minutes … kick-off + 3 hours`. Both bounds are fixed
  module constants (`_MATCH_LEADIN = timedelta(minutes=30)`,
  `_MATCH_WINDOW = timedelta(hours=3)`), not config.
- **Fixture store**: `_match_dates: set[date]` becomes `_matches: dict[date, list[datetime]]`
  — local match date → list of timezone-aware kick-off instants. An **empty list** means
  "match that day, kick-off time unknown".
- **Kick-off parsing** per TheSportsDB event: `strTimestamp` (UTC ISO) preferred → aware
  datetime; else `strTimeLocal` + `dateEventLocal` combined and attached to `_LOCAL_TZ`;
  else the date is recorded with an empty list. All stored kick-offs are aware; `resolve`
  compares them against the aware `now` as instants (DST-correct by construction).
- **Polling**: replace the single `daily_at(...)` call with
  `common.polling.every(poll_interval_hours * 3600, self._poll_once)`. `_poll_once` records
  **today and tomorrow**, and prunes `_matches` keys for dates earlier than today (local) —
  fixing the current unbounded growth. `start()` keeps its bootstrap check for today, then
  starts the interval task.
- **Config**: a new `poll_interval_hours` field (default `1`, must be a positive number)
  **replaces** `poll_time`; `_parse_poll_time` and `_DEFAULT_POLL_TIME` are deleted. The
  default API key stays `"3"` (TheSportsDB's shared public test key), still configurable.
- **Colours**: loaded once in `__init__` via
  `from hypervolt.led import THEMES_DIR, load_custom_effect` then
  `load_custom_effect(THEMES_DIR / "saints_fc.yaml")`, stored on the instance. A
  missing/malformed file raises during construction, so `load_extensions` logs it and the
  extension is treated as absent (ADR 0007). `_matchday_leds`, `_RED`, `_WHITE`,
  `_LED_COUNT` are deleted.
- **Wire effect name**: `"saints_fc"` (was `"saints_fc_matchday"`).
- **`resolve(now)`** (primary pass, top priority): let `d = now.astimezone(_LOCAL_TZ).date()`.
  `d not in _matches` → `None`; `any(ko − _MATCH_LEADIN <= now <= ko + _MATCH_WINDOW)` across
  `_matches[d]` → strip `LedTheme("saints_fc", leds=self._leds, always_on=True)`; else `None`.
- **`resolve_fallback(now)`**: same `d`; `d in _matches` and the window check above is
  `False` → strip `LedTheme("saints_fc", leds=self._leds, always_on=False)`; else `None`.
  The window check is factored into a shared helper used by both methods.

### No `main.py` / `load_extensions` change

The extension imports `THEMES_DIR` from `hypervolt.led` directly. The earlier draft's idea
of `main.py` resolving a `led_effects` directory and `load_extensions` injecting it into
every extension's config dict is dropped entirely.

### ADRs (one per relevant slice, committed during the BDD loop)

1. **Binary LED brightness; LED display decoupled from plug state.** `led.brightness`
   removed (`extra="forbid"`); a displaying theme is full brightness, nothing shown is off
   (no more plain-white-while-charging); the `always_on` flag (default `false`); always-on
   themes light an unplugged charger. **Supersedes ADR 0010.**
2. **`resolve_theme` fallback pass + Saints split priority.** The optional `resolve_fallback`
   hook and the second resolution pass; the Saints extension outranks all themes within
   `KO−30m … KO+3h` and sits below all themes (charging-gated) the rest of a match day.
   Deliberately breaks strict "extensions always outrank themes".
3. **A shipped extension may read a repo theme asset via `hypervolt.led`'s public API.**
   `THEMES_DIR` + `load_custom_effect` from a shipped extension narrows ADR 0006's
   "self-contained" to "no shared mutable state / no cross-extension coupling"; reading a
   repo asset through a documented API is fine.

## Testing Decisions

Test observable behaviour at the highest existing seam per changed module.

- **`tests/schedule/test_coordinator.py`** — already patches
  `schedule.coordinator.resolve_theme` and asserts on `charger_client.apply_led_state`, with
  `is_charging` / `led` knobs (`car_plugged` no longer matters to LED assertions). Cases:
  - `always_on=True` theme, not charging → `apply_led_state(1.0, effect, leds=...)`
  - `always_on=False` theme, not charging → `apply_led_state(0.0, None)`
  - `always_on=False` theme, charging → `apply_led_state(1.0, effect, leds=...)`
  - no theme, charging → `apply_led_state(0.0, None)` (was plain white)
  - `is_charging is None` → no `apply_led_state` call
  - the existing brightness tests and `test_falls_back_..._not_plugged_in` reworked / removed
- **`tests/extensions/test_saints_fc.py`** — against the httpx mock router already in place:
  - kick-off parse from `strTimestamp`, and from `strTimeLocal` + `dateEventLocal`; garbage
    → empty list
  - `resolve`: in-window (`always_on=True`, YAML colours); one second before `KO−30m` and one
    second after `KO+3h` → `None`; non-match date → `None`; double-header → in-window for
    either fixture's span; window bounds correct across a DST transition
  - `resolve_fallback`: match date + outside every window → `always_on=False` strip;
    in-window or non-match date → `None`; unknown-KO date → `resolve` `None` and
    `resolve_fallback` strip
  - interval polling via `every(...)` at the configured cadence; `_poll_once` records today
    and tomorrow and prunes past dates
  - colours equal `load_custom_effect(THEMES_DIR / "saints_fc.yaml")`; a missing file makes
    construction raise
- **`tests/hypervolt/test_led_resolve_extensions.py`** plus a new resolve-fallback test file
  — two-pass behaviour: a primary hit is returned as-is; on a primary miss the first
  extension `resolve_fallback` hit is returned; `always_on` survives the defensive copy; a
  `resolve_fallback` that raises is isolated and treated as `None`.
- **`tests/test_config.py`** — `always_on` defaults `False` on `CustomLedTheme` /
  `BuiltInLedTheme`; a non-bool raises; `led.brightness` now raises (`extra="forbid"`); an
  unknown key under `led:` raises.
- **`tests/hypervolt/test_led_load_custom_themes*.py` /
  `test_led_load_built_in_themes_for_config.py`** — the constructed `LedTheme` carries the
  configured `always_on`.
- `scripts/show_led_theme.py` stays a manual on-charger aid, not a CI gate (outside the
  coverage `source` dirs).

Prior art: the coordinator LED tests above; `tests/hypervolt/test_led_resolve_extensions.py`;
`tests/extensions/test_saints_fc.py`'s fixture-polling stubs and mock router.

## Out of Scope

- Making the Saints match window (the 30-minute lead-in, the 3-hour duration) configurable —
  fixed in code.
- Exposing `always_on` on the Saints extension config block — its window rule is fixed;
  `always_on` is a custom/built-in-theme field only.
- Per-theme brightness — brightness is binary (1.0 / 0.0), no config.
- Re-tuning the Saints colour map or the LED geometry — `themes/saints_fc.yaml` is final.
- Any change to `resolve_theme`'s *primary* walk order (extensions → custom → built-in).
- Vendoring — `themes/saints_fc.yaml` and `scripts/show_led_theme.py` already landed with
  PR #113; the research HTML pages were retired there.

## Further Notes

- `themes/saints_fc.yaml` is `default_colour: "#FFFFFF"` with red ranges
  `[1,2] [5,6] [21,22] [24,25] [39,40] [42,44] [48,50]`.
- Issue #91 previously moved the extension to daily polling to cut load on TheSportsDB's
  shared test key `"3"`. Hourly polling is a deliberate reversal, accepted for accuracy, and
  the interval is configurable (and an operator can drop in a personal key).
- The wire `effect_name` `"saints_fc"` could in principle alias a `custom_themes` entry
  literally named `saints_fc`. Harmless: both resolve to the same file and the same colours,
  so the charger's effect/leds change-detection sees no difference.
- `CONTEXT.md` was updated on this branch this session: **LED Theme** (two-pass resolution,
  binary brightness), **Always-on theme** (default `false`, plug state gates nothing),
  **Match window** (`KO−30m … KO+3h`, unknown kick-off is fallback-only), **LED Theme
  Extension** (a shipped extension may read a repo asset via `led.py`'s public API).
- This worktree was paused before Phase 3 and resumed for this work; it was rebased onto
  `main` at `a85cd68` (post-PR-#113) at the start of the session.
