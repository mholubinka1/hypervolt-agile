# LED Custom YAML Themes

## Problem Statement

The built-in seasonal effects (Halloween, Christmas, New Year) cover only the three occasions the
charger firmware already knows how to render. An operator who wants to mark a date that matters
to them personally — an anniversary, a national or cultural observance — has no way to do so
without the app supporting an entirely custom colour pattern, since the charger has no built-in
effect for anything outside those three.

## Solution

Operators can define a static 51-LED colour pattern in a YAML file and map it to a year-agnostic
date window in `config.yml`, using the same `custom_themes:` mechanism already reserved by the
first LED slice's config shape. The app converts the YAML's colours into the charger's generic
`steady_array` wire format and applies it during that window, following the same priority-stack
resolution (`resolve_theme`) and diffing (`apply_led_state`) machinery already built for built-in
effects. Five ready-made theme files ship with the app (peace/Ukraine flag, St George's Day, QE2
anniversary, Diana anniversary, St Patrick's Day) as both useful defaults and worked examples for
operators writing their own.

This is the second of three LED Theme Control slices, building directly on
`feature/led-brightness-and-builtin-themes`. The final slice (dynamic extensions) sits above this
one in the priority stack and is out of scope here.

## User Stories

1. As an operator, I want to define custom LED effects for dates that matter to me, so that I can
   extend the theme calendar without modifying the application code.

## Implementation Decisions

**`led_effects/*.yaml` format** — pure colour data, no logic:
```yaml
name: peace
default_colour: "#0057B7"      # hex, fills all 51 LEDs
segments:                       # optional overrides, later entries win on overlap
  - colour: "#FFD700"
    indices: [0, 1, 2]
  - colour: "#FFD700"
    ranges: [[10, 15]]
```
A loader function converts hex colours to normalised RGB floats and constructs the 51-element
`leds` array the charger's `steady_array` effect expects:
`[{"r": 0.0, "g": 0.35, "b": 0.73}, ...]`.

**`app/hypervolt/led.py` additions**:
- `load_custom_effect(path: Path) -> list[dict]` — parses one `led_effects/*.yaml` file into the
  51-element `leds` array. Raises on missing file or malformed content (missing
  `default_colour`, invalid hex, out-of-range index); the caller (config loading) is responsible
  for catching this and applying the log-and-skip policy below — `load_custom_effect` itself
  stays a pure, throwing parser.
- `resolve_theme(now, extensions, custom_themes)` — the middle tier (previously an empty list)
  is now populated: for each `CustomLedTheme` entry, in `config.yml` list order, check whether
  `now` falls in its date window; return the first match as a `LedTheme` with `effect_name`
  `"steady_array"` plus the loaded `leds` array. Falls through to built-ins if nothing in this
  tier matches. Extension tier (still empty in this slice) remains checked first, per the
  existing signature.

**`LedTheme` dataclass**: gains an optional `leds: list[dict] | None` field (only populated for
`steady_array` effects; `None` for named built-ins).

**`app/config.py`**: new `CustomLedTheme(BaseModel)` with `effect: str`, `start: str`, `end: str`.
`start`/`end` are validated via a pydantic `field_validator` against the `MM-DD` or
`MM-DD HH:MM` format at config-load time — a malformed date string is a config-authoring error in
the file the operator just edited, and fails loudly the same way every other `AppConfig` field
already does (see ADR 0007's contrast between core config's fail-fast validation and this
feature's own runtime graceful-degradation). `LedConfig` gains
`custom_themes: list[CustomLedTheme] = []`.

**Failure handling for the YAML file itself** (as opposed to the config entry referencing it):
resolved at startup, once, when custom themes are loaded — for each `CustomLedTheme`, attempt
`load_custom_effect(led_effects_dir / f"{effect}.yaml")`; on any exception, log an error naming
the effect and the exception, and drop that entry from the in-memory list `resolve_theme` walks.
The app starts regardless and every other configured theme, extension, and built-in continues to
work (ADR 0007). This is deliberately different from the date-string case above: a bad *value the
operator typed directly into config.yml* fails the config load outright (matches the rest of
`AppConfig`), whereas a bad *external YAML file* — which might be a shipped file, might be edited
independently of config.yml, and isn't itself part of the validated config schema — degrades
gracefully instead.

**`led_effects_dir` resolution**: `config_path.parent / "led_effects"` — no new CLI argument.
This mirrors `config.yml`'s own location exactly (both are declarative data an operator edits),
and in the deployed container resolves to `/config/led_effects` since `config.yml` already lives
at `/config/config.yml`.

**Shipped theme files** (`led_effects/`, five files): `peace.yaml`, `qe_ii.yaml`, `diana.yaml`,
`st_george.yaml`, `st_patricks.yaml`. Each is referenced from `config.yml.template` as a
commented-out example `custom_themes:` entry, not enabled by default.

**Dependency**: `pyyaml` (already a project dependency).

## Testing Decisions

**Stale — the `[[no-tests]]` convention referenced below was reversed 2026-08-23 (see
`tests-required`); this slice hasn't been re-planned for pytest yet. Re-derive real test seams
(likely: pure-logic tests on the YAML-parsing/theme-resolution code, plus a `ScheduleCoordinator`
test mocking `HypervoltChargerClient` at the boundary, matching
`feature-led-brightness-and-builtin-themes.md`'s Testing Decisions) when this slice is actually
picked up — don't implement it against the manual-verification list below.**

No automated tests, per project convention ([[no-tests]]) — verification through execution:
- Add a `custom_themes` entry pointing at one shipped YAML (e.g. `peace`), set the system clock
  inside its window, force `is_charging` True, and confirm `steady_array` is sent with the exact
  `leds` array the YAML's colours produce.
- Confirm a custom theme takes priority over a built-in preset when both match the same date (not
  expected to occur with the shipped files' dates, but exercise it with a temporary overlapping
  test entry).
- Point a `custom_themes` entry at a non-existent or deliberately malformed YAML file and confirm:
  the app still starts, an error is logged naming the effect, and other themes/built-ins still
  resolve correctly.
- Put a malformed date string (e.g. `"13-45"`) directly in a `custom_themes` entry and confirm
  the app fails to start with a clear pydantic validation error, same as any other bad
  `config.yml` value.

## Out of Scope

- Dynamic `LedThemeProvider` extensions — final slice, `feature/led-theme-extensions`.
- Any UI or CLI tooling for authoring theme YAML files — hand-written by the operator.
- Validating YAML colour data beyond what `load_custom_effect` needs to construct the `leds`
  array (e.g. no colour-contrast or accessibility checking).

## Further Notes

Depends on `feature/led-brightness-and-builtin-themes` having merged first — this slice extends
`resolve_theme`'s existing signature and `LedConfig`, it doesn't introduce either from scratch.
