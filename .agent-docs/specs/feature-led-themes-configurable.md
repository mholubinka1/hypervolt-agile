# Configurable Built-in LED Themes

## Problem Statement

The three built-in LED presets (`halloween_mode`, `christmas_mode`, `party_mode`) are hardcoded
in `app/hypervolt/led.py` with fixed windows and no way to opt individual ones out — the whole
`led:` feature can be paused via `enabled: false`, but there's no way to run with, say, only
`christmas_mode` active. Separately, LED themes only ever show while the car is actively
charging: as soon as `is_charging` goes `False`, the LEDs go dark regardless of whether a themed
window (e.g. Christmas week) is still open. An operator who wants the charger to visibly mark a
seasonal occasion the whole time it's plugged in — not just during the minutes it happens to be
drawing current — currently can't.

## Solution

Built-in themes become config-driven and fully opt-in: an effect only ever fires if
`config.yml`'s `led.built_in_themes` explicitly lists it with a `start`/`end` window. No entry
for an effect means it never runs — there is no implicit hardcoded fallback at runtime. This
mirrors how `led.custom_themes` already behaves (presence in the list is what enables a theme),
so both mechanisms now follow one consistent rule.

Separately, LED display decouples from `is_charging`: whenever a theme resolves (built-in,
custom, or extension) and the car is plugged in, its effect shows at the configured brightness
regardless of whether the car happens to be charging at that instant. If nothing resolves, the
existing charging-gated plain-brightness/off behaviour is unchanged.

Both are real reversals of decisions made in the original `feature-led-brightness-and-builtin-themes`
spec (built-ins as an always-available hardcoded tier; LED display gated on `is_charging`), so
this work records two ADRs.

## User Stories

1. As an operator, I want to choose which of the built-in seasonal effects are active and when,
   so that I'm not stuck with all three or none.
2. As an operator, I want a themed LED window (e.g. Christmas week) to stay visible on the
   charger the whole time the car is plugged in, not just during active charging, so that the
   effect actually functions as a seasonal decoration rather than a rare coincidence.
3. As a developer reading this code later, I want the shift away from an implicit built-in
   fallback recorded as a decision, not just a diff, so that a future "why doesn't Christmas mode
   just work by default anymore?" has an answer.

## Implementation Decisions

**`app/config.py`**: factor `CustomLedTheme`'s `effect`/`start`/`end` fields and its two
window-validity validators (`must_be_a_valid_window_date`, `end_must_be_after_start`) into a
shared base, e.g. `_WindowedLedTheme(BaseModel)`. `CustomLedTheme(_WindowedLedTheme)` keeps its
existing `must_not_collide_with_a_built_in_theme` validator (effect must NOT be a reserved
built-in name) unchanged. A new `BuiltInLedTheme(_WindowedLedTheme)` gets the inverse validator —
effect MUST be one of the three reserved built-in names (`_RESERVED_LED_EFFECT_NAMES`, already
derived from `led.py`'s built-in list). `LedConfig` gains `built_in_themes: list[BuiltInLedTheme]
= []`, following the same empty-list-means-nothing-enabled default as `custom_themes`.

**`app/hypervolt/led.py`**: rename `BUILT_IN_THEMES` to `DEFAULT_BUILT_IN_THEMES` — it stops
being an implicit runtime fallback and becomes reference data: the source of
`_RESERVED_LED_EFFECT_NAMES` in `config.py`, and the values documented (commented) in
`config/config.yml.template`. `resolve_theme(now, extensions=(), custom_themes=(),
built_in_themes=())` gains the `built_in_themes` parameter, replacing the internal
`_resolve_from(now, BUILT_IN_THEMES)` call with `_resolve_from(now, built_in_themes)` — the
parameter defaults to `()`, not the old global, so a caller that passes nothing gets no built-in
themes at all. Priority order is unchanged (extensions beat custom beat built-in). A new
`load_built_in_themes_for_config(led_config: LedConfig | None) -> list[tuple[LedTheme, Window,
Window]]` mirrors `load_custom_themes_for_config`'s shape: `None` config or an empty
`built_in_themes` list returns `[]`; otherwise each entry is parsed via `parse_window_date` into
a `LedTheme(effect_name=entry.effect, leds=None)` plus its window tuple, in list order. Unlike
custom themes, no file I/O or per-entry try/except is needed — a `BuiltInLedTheme`'s `effect` is
already validated by pydantic to be one of the three known names, so there's nothing that can
fail at load time the way a missing YAML file can.

**`app/main.py`**: call `load_built_in_themes_for_config(app_config.led)` alongside the existing
`load_custom_themes_for_config` call, and pass the result into `ScheduleCoordinator` the same way.

**`app/schedule/coordinator.py`**: `ScheduleCoordinator.__init__` gains a
`built_in_themes: Sequence[tuple[LedTheme, Window, Window]] = ()` parameter, stored as
`self._built_in_themes` and threaded into `resolve_theme(..., built_in_themes=self._built_in_themes)`.

`_apply_led_state`'s gating changes shape. Today: `is_charging is None` → return; `not
is_charging` → force off; only when charging does it resolve a theme. New: after the existing
`is_charging is None` guard, resolve the theme unconditionally (extensions have no I/O in
`resolve()`'s hot path per ADR 0005, so resolving every cycle regardless of charging state is
cheap). Then:
- If a theme resolved **and** `charger_state.car_plugged is True` (matching the existing
  `is True` convention used by `_can_push()`, so an unknown/`None` plugged state behaves like
  "not plugged"): apply it at `led_config.brightness`.
- Otherwise, fall back to the existing charging-gated behaviour: `led_config.brightness` with no
  effect while charging, `0.0` with no effect while not.

This does not yet add a way for an individual theme to skip the `car_plugged` check too (an
"always on even unplugged" override) — no theme in this slice needs it. That's tracked separately
in `FEATURES.md` Feature 23 (`saints_fc` needs it; blocked on a separate physical LED map
prerequisite unrelated to this slice).

**`config/config.yml.template`**: document the new `built_in_themes` list under the existing
commented `led:` block, showing all three effects at their current default windows
(`halloween_mode` 31 Oct–1 Nov 06:00, `christmas_mode` 24–31 Dec 06:00, `party_mode` 31 Dec
06:00–1 Jan 06:00) as a commented example, with a note that listing an effect is what enables it.
The `custom_themes` section stays as a generic (no worked example) commented placeholder — no
`custom_themes` examples ship in this slice.

**Deployed `config/config.yml`**: turn the `led:` block on for real (`enabled: true`, the default
`brightness: 0.5`, and all three `built_in_themes` entries at their default windows) — this file
is gitignored/untracked and never flows through the branch/PR process, so it's edited directly
once the rest of this work is merged, not as part of the tracked diff.

**`README.md`**: add a new subsection under `## Configuration` documenting the three built-in
themes with a runnable example `led:` block, matching the existing style of the top-level config
example already in that section.

**ADRs**: two, both meeting the "hard to reverse / surprising without context / genuine
trade-off" bar —
1. Built-in themes move from an always-available hardcoded tier to fully opt-in config, with no
   implicit fallback. The genuine alternative (partial merge: configuring one effect's window
   without affecting the other two's hardcoded defaults) was considered and rejected — explicit
   opt-in was chosen so an operator's `config.yml` is a complete, self-documenting statement of
   what's active, rather than requiring cross-referencing hardcoded source to know what's
   currently on.
2. LED display decouples from `is_charging` (still gated on `car_plugged`). The original spec's
   `Out of Scope` section explicitly called the off-while-not-charging behaviour deliberate and
   non-configurable — this reverses that call specifically for the case where a theme is active,
   while leaving the no-theme-active case (plain brightness while charging, off while not)
   unchanged.

## Testing Decisions

**`tests/test_config.py`**: add `BuiltInLedTheme` coverage mirroring the existing
`CustomLedTheme` tests — valid built-in name accepted, non-built-in name rejected (inverse of
`must_not_collide_with_a_built_in_theme`), and confirm the shared date/window validators (invalid
date format, 29 Feb rejected, end-before-start rejected, year-wrap end-before-start accepted)
still apply post-refactor by running the same parametrised cases against both classes where
practical.

**`app/hypervolt/led.py`**: `load_built_in_themes_for_config` — `None` config returns `[]`; empty
`built_in_themes` returns `[]`; a populated list returns the expected `LedTheme`/`Window` tuples
in order, mirroring `load_custom_themes_for_config`'s existing test shape.

**`resolve_theme`**: the ~10 existing call sites in `tests/hypervolt/test_led.py` that call
`resolve_theme(now)` bare (relying on the old implicit global) need `built_in_themes=
DEFAULT_BUILT_IN_THEMES` added explicitly, since the parameter now defaults to `()`. Add one new
case confirming `resolve_theme(now)` with no `built_in_themes` argument returns `None` even
during a date that would have matched the old hardcoded default — this is the behavioural
assertion that most directly proves the opt-in change.

**`ScheduleCoordinator._apply_led_state`**: extend `tests/schedule/test_coordinator.py`'s
existing LED-state tests (which already mock `HypervoltChargerClient` at the boundary) with: a
theme resolves while `car_plugged=True` and `is_charging=False` → theme applied anyway (the core
new behaviour); a theme resolves while `car_plugged=False` → falls back to the old
charging-gated behaviour, not the theme; no theme resolves → behaviour is bit-for-bit identical
to before this change (charging → brightness/no effect, not charging → off). Also add the new
`built_in_themes` constructor parameter to whatever fixture/helper currently builds a
`ScheduleCoordinator` for these tests.

Manual verification: none required beyond what Feature 19's original slice already covered
(`effect_name` wire format is unchanged) — this slice only changes *when* a resolved theme is
sent, not the wire payload itself.

## Out of Scope

- Any new `custom_themes` YAML files (`valentines`, `bonfire_night`, `pride`) — deferred, see
  `FEATURES.md` Feature 23; blocked on a confirmed physical LED index map.
- The `saints_fc` restripe and its "always on even when unplugged" override — same Feature 23,
  same blocker.
- Any of the six additional app-provided presets (Red Alert, Turbo Boost, Flag, Peace, QEII
  tribute, St Patricks) — see `FEATURES.md` Feature 21; wire values unconfirmed, and the user has
  explicitly said no more built-in themes beyond the existing three for now.
- The physical LED index calibration tool — see `FEATURES.md` Feature 22.
- A per-theme "always on regardless of plug state" override mechanism — not needed by anything
  shipping in this slice; the base "decouple from charging, still require plugged in" behaviour
  is in scope, the stronger override is not.

## Further Notes

- `extensions/saints_fc.py` was substantially changed on `main` since this branch's design
  discussion started (switched from football-data.org to TheSportsDB, fixed-time daily polling
  instead of interval polling — see commits `83cf390`, `96c0d3c` and their review follow-ups).
  This branch was rebased onto that state. `_matchday_leds()`'s striping logic itself (`i % 2`
  alternation across all 51 indices) is unchanged, so the Feature 23 notes describing it as a
  future fix target are still accurate.
- See `.agent-docs/specs/feature-led-brightness-and-builtin-themes.md` for the original built-in
  themes spec this work partially reverses, and ADRs 0005–0008 for the surrounding LED
  architecture (extension lifecycle, isolated mounting, graceful config degradation, async
  `resolve_theme`).
