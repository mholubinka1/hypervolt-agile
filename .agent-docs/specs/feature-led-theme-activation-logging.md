# LED theme activation/deactivation logging

## Problem Statement

On 2026-09-08 a Southampton FC match was in progress and the operator's app showed no LED
theme and zero brightness. Working out why meant reading the scheduler log — and the log
said nothing useful about LED themes. The only LED lines it carries come from the charger
client: `Setting LED brightness to 1.0` and `Setting LED effect to steady_array`. Neither
names the theme, neither says when it started, neither says how long it was expected to
last. There is no way to answer "which LED Theme is on the ring right now, and since when"
or "did the Saints strip ever come on today, and for how long" from the log alone.

## Solution

The schedule coordinator — the one place that decides, every poll cycle, what the ring
shows — logs a line each time the displayed LED Theme changes:

- When a theme first lights the ring: `LED theme 'saints_fc' active until 2026-09-08 21:45
  (~2h55m)` — the effect name, and where the matching source knows it, the instant the
  theme is expected to stop plus a rough time-to-go. When the source has no firm end the
  line is just `LED theme 'saints_fc' active`.
- When the ring goes dark and no new theme replaces it: `LED theme 'saints_fc' cleared
  after 2h58m` — the effect name and how long it was actually lit.
- When one theme replaces another in the same cycle: a single line for the incoming theme
  carrying both its own predicted end and what it displaced — `LED theme 'nye' active until
  2026-09-09 06:00 (~9h) — replaced 'saints_fc' after 3h01m`.

"Displayed" is the trigger, not "resolved": a charging-gated theme that is resolved but
dark because the car is idle is not active and logs nothing until it actually lights.
While the same theme stays on the ring, nothing is logged — only transitions.

## User Stories

1. As the operator diagnosing a dark ring during a match, I want the log to tell me whether
   a Saints theme was ever displayed today and for how long, so that I can tell "the
   fixture was never detected" apart from "the theme came and went with a charge session".
2. As the operator, I want each newly displayed theme logged with its name and expected end
   time, so that a theme still lit long past its window (a stuck resolve, a missed
   deactivation) is obvious from the log.
3. As the operator, I want the theme's measured lit duration logged when it goes dark, so
   that I can see at a glance how long the ring actually showed it.
4. As the operator, I want a theme swap recorded as one line naming both themes, so that the
   log reads as a single timeline without a spurious "off then on" flicker.
5. As the operator, I want steady state to stay silent, so that a theme lit for hours
   produces one line, not one per poll cycle.
6. As the operator, I want the first cycle after a restart to re-announce whatever theme is
   on the ring, so that a log that begins mid-session still establishes current state.
7. As a developer, I want the Saints extension to report its own match-window end, so that
   the "active until" on the log line reflects `kick-off + 3h` rather than being blank for
   the one theme this feature was built to observe.

## Implementation Decisions

### `active_until` on the resolved theme (ADR 0020)

- `LedTheme` gains `active_until: datetime | None = None`. It is a property of the
  resolution result, not of a catalogue entry — the default keeps it invisible on
  `DEFAULT_BUILT_IN_THEMES` and on the `(LedTheme, Window, Window)` tuples the coordinator
  holds. It is never consulted for display or wire state; it exists only for the
  transition log.
- `resolve_theme` stamps it onto the fresh defensive copy it already builds:
  - calendar match — `_resolve_from` returns the matched window's end alongside the theme
    (`(LedTheme, datetime)`), and `resolve_theme` stamps that end.
  - extension match (primary or fallback pass) — lifted from `active_until` on the
    `LedTheme` the provider returned.
- `LedThemeProvider.resolve` / `resolve_fallback` implementations may set `active_until` on
  their returned theme. Leaving it `None` is valid and means "no firm end known".

### Saints FC extension

- `resolve()` sets `active_until` to the latest `kick-off + match window` across the
  fixtures whose window currently contains `now` (a rare double-header takes the later
  end).
- `resolve_fallback()` leaves `active_until` as `None` — the rest-of-day charging-gated
  fallback has no firm end; it lasts as long as the car keeps charging.

### Coordinator transition tracking

- `ScheduleCoordinator` gains `_active_theme_name: str | None` and
  `_active_theme_since: datetime | None`, both starting empty. No persistence across
  process restarts.
- A helper — `_note_led_theme(new_name, active_until, now)` — is called on both outcomes of
  `_apply_led_state`: with `_target.effect_name` on the branch that applies brightness 1.0,
  and with `None` on the branch that clears to 0.0. It reuses the single
  `datetime.now(TIMEZONE)` already taken for `resolve_theme` (lifted into a local so both
  callers share one instant).
- Transition rules, keyed on `new_name` vs `_active_theme_name` (identity is `effect_name`
  only):

  | previous | new | logged (INFO) |
  |---|---|---|
  | `None` | `A` | `LED theme 'A' active[ until <ts> (~<dur>)]` |
  | `A` | `None` | `LED theme 'A' cleared after <dur>` |
  | `A` | `B` | `LED theme 'B' active[ until <ts> (~<dur>)] — replaced 'A' after <dur>` |
  | `A` | `A` | nothing |
  | `None` | `None` | nothing |

- `_apply_led_state`'s early returns (LED config absent or disabled, `is_charging` still
  `None`) return before the helper is reached, so tracked state is untouched on those
  cycles. The disconnected-websocket case does not call `_apply_led_state` at all.
- The predicted-end timestamp is rendered in charger-local time as `YYYY-MM-DD HH:MM` via
  `.astimezone(TIMEZONE)`. The `until` clause and its `(~<dur>)` are omitted together when
  `active_until` is `None`.

### `format_duration`

- `format_duration(td: timedelta) -> str` added to `common/utils.py`: `2h58m`, `47m`,
  `38s`; a zero or negative delta renders as `0s`. Used for both the measured lit duration
  and the `(~…)` time-to-go. Minutes and seconds are always two digits when a larger unit
  precedes them.

## Testing Decisions

Behaviour, not internals: assert the log lines that come out of the coordinator for a given
sequence of poll cycles, and the `active_until` value on the theme `resolve_theme` hands
back — not the private tracking fields.

- **`tests/schedule/test_coordinator.py`** (primary seam). Drive `_apply_led_state` across
  cycles with the existing `_frozen_now` helper and capture `caplog`. Cover every row of
  the transition table: first activation (with and without a known `active_until`),
  deactivation naming the measured duration, A→B swap as one line naming both, steady-state
  silence across repeated cycles, the first-cycle-after-restart re-announce (a fresh
  coordinator with the ring already "on"), and silence on the `is_charging is None`
  early-return. Include at least one end-to-end case using the real `resolve_theme` and a
  real seeded `SaintsFcExtension` so the `kick-off + 3h` end reaches the log string. Prior
  art: the existing Saints window / fallback coordinator tests in this file already use
  `_frozen_now` and a real seeded extension.
- **`tests/hypervolt/test_led_resolve_*.py`**. `resolve_theme` returns `active_until` set
  to the matched window end for a calendar theme, to the provider-supplied value for an
  extension match on both the primary and fallback pass, and `None` when the matching
  source supplied none. The three existing `assert theme == _PEACE` equality assertions in
  `test_led_resolve_custom_themes.py` move to field assertions (or compare against
  `dataclasses.replace(_PEACE, active_until=<end>)`), since the returned copy now carries a
  populated `active_until`.
- **`tests/extensions/test_saints_fc.py`**. `resolve()` inside a match window returns a
  theme whose `active_until` is `kick-off + 3h`; with two same-day fixtures whose windows
  overlap `now`, it is the later end; `resolve_fallback()` returns `active_until` `None`.
  Prior art: the file already seeds `_matches` directly and patches `saints_fc.datetime`.
- **`tests/common/test_utils.py`**. `format_duration` across hours+minutes, minutes only,
  seconds only, exact-hour, zero, and negative inputs.

Coverage ratchet (ADR 0002) must stay at or above 80%; every new branch above is
exercised.

## Out of Scope

- Changing what the ring displays or when — this is logging only. `charger.py` and its
  wire-level `Setting LED brightness/effect` lines are untouched.
- Persisting transition state across process restarts — a restart deliberately re-announces
  from empty.
- Logging the Saints match-window ↔ fallback gate change while `effect_name` stays
  `saints_fc` — the ring does not visibly change, so it is not a transition.
- Re-deploying the corrected Saints extension to the Pi — already done by hand during the
  incident this spec came out of.

## Further Notes

- The incident's root cause (a stale, charging-gated build of the Saints extension on the
  Pi) is fixed separately. This spec is the observability gap it exposed: even with the
  right code running, the log could not have shown the operator what the ring was doing.
- The predicted `active_until` is advisory — a higher-priority theme can preempt a calendar
  window before its end. The log line is describing intent at activation time, and the
  eventual `cleared after <dur>` / `replaced 'A' after <dur>` line records what actually
  happened.
