# LED Brightness and Built-in Seasonal Themes

## Problem Statement

The charger's LEDs give no visual signal that a charge session is actually running — from a
glance at the device, an active charge and an idle one look identical. Separately, the charger
sits idle for most of the year outside its actual job, and its LEDs are an unused surface that
could mark occasions (Halloween, Christmas, New Year) without any effort from the operator once
configured.

## Solution

While the car is charging, the app sets the charger's LEDs to 50% brightness, giving a clear at-a-glance
signal that charging is active. If the current date falls within a known seasonal window, a themed
LED effect (`halloween_mode`, `christmas_mode`, `party_mode`) is applied on top of that brightness
instead of plain white. When charging stops, brightness returns to 0 (LEDs off). The whole feature
is opt-in via a `led:` block in `config.yml`, and can be paused with `enabled: false` without
losing any configuration.

This is the first of three LED Theme Control slices. It ships brightness control and the three
built-in seasonal effects on their own, but shapes the underlying priority-stack architecture so
that two later slices — operator-defined custom YAML themes, then dynamic extensions — slot in
ahead of built-ins without reworking this code.

## User Stories

1. As an operator, I want the charger LEDs to glow at 50% while charging, so that I have a clear
   visual indicator that charging is active.
2. As an operator, I want a thematically appropriate LED effect applied during seasonal dates
   while charging, so that charging is a small occasion for celebration.
3. As an operator, I want to be able to pause LED control without losing my configuration, so
   that I can temporarily disable it without having to re-enter it later.

## Implementation Decisions

**Charger wire contract**: the charger exposes two independent LED controls via `sync.apply`:
`brightness` (float 0.0–1.0) and `effect_name` (string). Both are one-way pushes — the charger
echoes `brightness` back via `sync.snapshot`/`sync.apply` pushes (already parsed into
`HypervoltChargerState.led_brightness`, defaulting to `None` when absent — this was already
fixed in the current codebase and needs no further change), but it never echoes the active
effect. The active effect must therefore be tracked locally.

**`app/hypervolt/led.py`** (new module):
- `LedTheme` — a small dataclass carrying `effect_name: str` (the wire value to send).
- `BUILT_IN_THEMES` — a hardcoded list of `(effect_name, start, end)` entries for the three
  seasonal windows below, in London local time, year-agnostic (`MM-DD` or `MM-DD HH:MM`,
  defaulting to `00:00`; a window whose end month is earlier than its start month wraps into the
  next year):
  - `halloween_mode`: 31 Oct 00:00 → 1 Nov 06:00
  - `christmas_mode`: 24 Dec 00:00 → 31 Dec 06:00
  - `party_mode`: 31 Dec 06:00 → 1 Jan 06:00
- `resolve_theme(now, extensions=(), custom_themes=()) -> Optional[LedTheme]` — walks a
  three-tier priority stack and returns the first match: registered extensions (highest
  authority) → custom YAML themes → built-in presets (lowest). This slice populates only the
  built-in tier; `extensions` and `custom_themes` parameters exist now so the function's shape
  does not change when the next two slices land (see ADR 0005 and ADR 0006 for why extensions in
  particular are designed to never block this resolution with I/O).

**`app/hypervolt/charger.py`** (`HypervoltChargerClient`): new `apply_led_state(brightness:
float, effect_name: str | None) -> None` method, following the same steady-state diffing pattern
already used by `apply_schedule()`/`_last_pushed_sessions`. Adds `_current_led_effect: str |
None` as new instance state (the effect last pushed to the charger; `None` means no effect
active), since the charger never echoes it back. Pushes brightness only when it differs from
`charger_state.led_brightness`; pushes the effect only when it differs from
`_current_led_effect`; sends nothing when both already match.

**Corrected 2026-08-23**: `HypervoltProtocol` doesn't own outbound request-building — it only
parses inbound responses (`_on_*_response`) plus two thin wrappers (`sync()`,
`get_charging_schedule()`) that themselves just call back out to the websocket layer. The actual
`sync.apply` push pattern lives on `HypervoltWebSocketClient` directly: `set_lock_state()` builds
and sends the message itself via `_send_message()`, with no `HypervoltProtocol` involvement.

**`app/hypervolt/client/websocket.py`** (`HypervoltWebSocketClient`): new `set_led_brightness(brightness:
float) -> None` and `set_led_effect(effect_name: str) -> None` (the caller in `HypervoltChargerClient`
always resolves the `"none"` sentinel before calling — see "Clearing an active effect" below — so
this method never needs to accept `None`), each building and sending a `sync.apply` message with
the relevant param, following `set_lock_state()`'s exact pattern — no `HypervoltProtocol` changes
needed for this slice.

**`app/schedule/coordinator.py`** (`ScheduleCoordinator`): new `_apply_led_state()` method,
called from `run()` unconditionally after `refresh()` — critically, **not** nested inside the
`if self._can_push():` block that gates lock control and schedule pushes. LED state is a visual
side-effect of the charger's actual physical charging state, not a scheduling action the app
controls; it must reflect reality even when the scheduler itself is holding back (e.g. released
state, or a plug-and-charge session the app didn't initiate). Gating is solely:
`config.led is not None and config.led.enabled and charger_state.is_charging is not None`.
When `is_charging` is `True`: resolve the target theme via `led.py`'s `resolve_theme(now)` and
call `charger_client.apply_led_state(config.led.brightness, target.effect_name if target else
None)`. When `is_charging` is `False`: call `charger_client.apply_led_state(0.0, None)` — the
configured brightness only applies while actually charging; the off state is never configurable.

**`app/config.py`**: new `LedConfig(BaseModel)` with `enabled: bool = True` (present-but-omitted
`enabled` key defaults to on; the `led:` block being entirely absent is what disables the feature
— an explicit `enabled: false` pauses it while retaining any future `custom_themes`/`extensions`
config the operator has already written for the later slices) and `brightness: float = Field(0.5,
gt=0, le=1)` (**changed 2026-08-23**, was hardcoded — omitting the key still defaults to 50%,
matching the `gt`/`le` bound style already used on `Schedule`'s fields). Add `led: LedConfig |
None = None` to `AppConfig`.

**`config/config.yml.template`**: document the `led:` block with `enabled` and `brightness`.

**Wire formats**:
```json
{"method": "sync.apply", "params": {"brightness": 0.5}}
{"method": "sync.apply", "params": {"effect_name": "halloween_mode"}}
{"method": "sync.apply", "params": {"effect_name": "none"}}
```

**Clearing an active effect (added 2026-08-23)**: the literal string `"none"` is the wire
sentinel that tells the charger to stop rendering a named effect and fall back to plain
brightness — confirmed against a reference Hypervolt API client (a local, gitignored file, not
part of this repo's history; matches the app's own UI, where selecting an effect exposes a
"Disable" action). `apply_led_state` sends `effect_name="none"` whenever the resolved target
transitions from a real effect to none, diffed the same way as any other effect change — not
sent on every cycle, only on that transition. Without this, a theme would otherwise be left
showing on the physical charger indefinitely once its window closed, since nothing else in the
protocol clears it.

## Testing Decisions

**Superseded 2026-08-23**: this codebase's earlier "no automated tests" convention has been
reversed (see the `tests-required` memory) — pytest is now the test framework, seeded on
`common/`, `config.py`, and `schedule/builder.py` in `chore/introduce-test-coverage`, with a CI
coverage-diff gate that must not regress. This slice follows the same pattern rather than relying
solely on manual execution.

Two natural seams, chosen as the highest/deepest available for each concern:

- **`app/hypervolt/led.py`'s `resolve_theme(now, extensions=(), custom_themes=())`** — pure
  function, no I/O, directly testable with fixed `datetime`s. Covers every date-driven acceptance
  criterion on #76 without touching the scheduler at all: each of the three seasonal windows
  (inside, and just outside each boundary), the year-wrap case (`party_mode` spanning New Year's
  Eve into January), and the "no window active" case returning `None`.
- **`ScheduleCoordinator._apply_led_state()`** (exercised via `run()` or called directly) — the
  seam for every criterion about *whether and what* gets pushed to the charger: brightness
  0.5/0.0 on the `is_charging` True/False transition, no redundant push when state already
  matches, the `led.enabled`/absent-`led:`-block gating, and LED control firing regardless of
  `_can_push()`. `HypervoltChargerClient` is mocked here — it's the system boundary (owns the
  real websocket I/O to the physical charger) — asserting `apply_led_state(brightness,
  effect_name)` was called with the expected arguments, or not called at all. Nothing above this
  boundary (the coordinator's own gating/diffing logic) is mocked.

`apply_led_state()`'s own diffing behaviour (only pushing what changed) is exercised indirectly
through the coordinator tests above rather than tested in isolation — it has no interesting logic
of its own beyond the diff-and-call pattern `apply_schedule()` already established, so a dedicated
unit test would just restate the implementation.

Manual verification is still worthwhile for one thing no unit test can cover: physically observing
the LEDs. Confirm on real hardware once, mainly as a wire-format sanity check (does `sync.apply`
with `effect_name` actually produce the expected visual effect) — this is a one-time check, not a
substitute for the automated coverage above.

## Out of Scope

- Custom YAML-defined themes (`led_effects/`, `custom_themes` config) — next slice,
  `feature/led-custom-yaml-themes`.
- Dynamic `LedThemeProvider` extensions (`extensions/`, `/extensions` mount) — final slice,
  `feature/led-theme-extensions`.
- ~~Configurable brightness — hardcoded at 0.5, no story requires tuning it.~~ **Reversed
  2026-08-23** — brightness is now a `LedConfig` field (`brightness: float = Field(0.5, gt=0,
  le=1)`, see Implementation Decisions), defaulting to 50% when omitted.
- Any reset-to-default push when `enabled` transitions to `false` — the LEDs stay frozen in
  whatever they last showed, by design.

## Further Notes

- The `led_brightness` defaulting bug described in the original feature write-up
  (`FEATURES.md`) is already fixed in the current codebase — `protocol.py` already defaults it
  to `None`, not `0.0`, when absent from the wire response. No change needed here.
- See ADR 0005 (extension polling lifecycle) and ADR 0007 (LED config degrades gracefully vs.
  core config fail-fast) for the reasoning behind `led.py`'s shape, even though this slice's own
  failure surface is minimal (a single boolean).
