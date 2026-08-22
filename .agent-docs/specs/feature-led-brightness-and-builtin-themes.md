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
  does not change when the next two slices land (see ADR 0002 and ADR 0003 for why extensions in
  particular are designed to never block this resolution with I/O).

**`app/hypervolt/charger.py`** (`HypervoltChargerClient`): new `apply_led_state(brightness:
float, effect_name: str | None) -> None` method, following the same steady-state diffing pattern
already used by `apply_schedule()`/`_last_pushed_sessions`. Adds `_current_led_effect: str |
None` as new instance state (the effect last pushed to the charger; `None` means no effect
active), since the charger never echoes it back. Pushes brightness only when it differs from
`charger_state.led_brightness`; pushes the effect only when it differs from
`_current_led_effect`; sends nothing when both already match.

**`app/hypervolt/client/protocol.py`** (`HypervoltProtocol`): new request methods
`set_led_brightness(brightness: float)` and `set_led_effect(effect_name: str | None)`, each
sending a `sync.apply` message with the relevant param.

**`app/hypervolt/client/websocket.py`** (`HypervoltWebSocketClient`): expose the two protocol
methods as public methods, following the existing pattern for `set_lock_state` /
`set_charging_schedule`.

**`app/schedule/coordinator.py`** (`ScheduleCoordinator`): new `_apply_led_state()` method,
called from `run()` unconditionally after `refresh()` — critically, **not** nested inside the
`if self._can_push():` block that gates lock control and schedule pushes. LED state is a visual
side-effect of the charger's actual physical charging state, not a scheduling action the app
controls; it must reflect reality even when the scheduler itself is holding back (e.g. released
state, or a plug-and-charge session the app didn't initiate). Gating is solely:
`config.led is not None and config.led.enabled and charger_state.is_charging is not None`.
When `is_charging` is `True`: resolve the target theme via `led.py`'s `resolve_theme(now)` and
call `charger_client.apply_led_state(0.5, target.effect_name if target else None)`. When
`is_charging` is `False`: call `charger_client.apply_led_state(0.0, None)`.

**`app/config.py`**: new `LedConfig(BaseModel)` with a single field, `enabled: bool = True`
(present-but-omitted `enabled` key defaults to on; the `led:` block being entirely absent is what
disables the feature — an explicit `enabled: false` pauses it while retaining any future
`custom_themes`/`extensions` config the operator has already written for the later slices). Add
`led: LedConfig | None = None` to `AppConfig`.

**`config/config.yml.template`**: document the `led:` block with just `enabled`.

**Wire formats**:
```json
{"method": "sync.apply", "params": {"brightness": 0.5}}
{"method": "sync.apply", "params": {"effect_name": "halloween_mode"}}
```

## Testing Decisions

This codebase does not write automated tests ([[no-tests]] convention) — verification is through
execution. For this slice specifically:
- Force `is_charging` True/False via a real charge session (or a short-circuited local run) and
  confirm brightness 0.5/0.0 is sent exactly once per transition, with no redundant pushes on
  subsequent cycles when state already matches (watch debug logs for `sync.apply` sends).
- Run with the system clock set inside each of the three theme windows (and just outside their
  boundaries) and confirm the correct `effect_name` is sent, and that no effect is sent outside
  all windows.
- Confirm `led.enabled: false` and an absent `led:` block both produce zero LED messages.
- Confirm a config change from `enabled: true` to `enabled: false` mid-charge leaves the
  currently-showing effect frozen (no reset command sent) — physically observe the LEDs.

## Out of Scope

- Custom YAML-defined themes (`led_effects/`, `custom_themes` config) — next slice,
  `feature/led-custom-yaml-themes`.
- Dynamic `LedThemeProvider` extensions (`extensions/`, `/extensions` mount) — final slice,
  `feature/led-theme-extensions`.
- Configurable brightness — hardcoded at 0.5, no story requires tuning it.
- Any reset-to-default push when `enabled` transitions to `false` — the LEDs stay frozen in
  whatever they last showed, by design.

## Further Notes

- The `led_brightness` defaulting bug described in the original feature write-up
  (`FEATURES.md`) is already fixed in the current codebase — `protocol.py` already defaults it
  to `None`, not `0.0`, when absent from the wire response. No change needed here.
- See ADR 0002 (extension polling lifecycle) and ADR 0004 (LED config degrades gracefully vs.
  core config fail-fast) for the reasoning behind `led.py`'s shape, even though this slice's own
  failure surface is minimal (a single boolean).
