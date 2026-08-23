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
  51-element `leds` array. Raises on missing file or malformed content (missing `default_colour`,
  a segment missing `colour`, invalid hex, out-of-range index — including negative indices, added
  2026-08-24 after a review pass caught that a negative index silently wrapped to the last LED
  via Python list semantics instead of raising); the caller (config loading) is responsible for
  catching this and applying the log-and-skip policy below — `load_custom_effect` itself stays a
  pure, throwing parser.
- `resolve_theme(now, extensions, custom_themes)` — the middle tier (previously an empty list)
  is now populated: `custom_themes` is `Sequence[tuple[LedTheme, Window, Window]]`, reusing the
  exact same `(payload, start, end)` shape and `_window_for_year`/boundary-check loop
  `BUILT_IN_THEMES` already uses — for each entry, in `config.yml` list order, check whether `now`
  falls in its date window; return the first match's `LedTheme` (already fully built with its
  `leds` array — see below). Falls through to built-ins if nothing in this tier matches.
  Extension tier (still empty in this slice) remains checked first, per the existing signature.
  **Refactored 2026-08-24**: `BUILT_IN_THEMES` now stores `LedTheme` instances directly (was
  raw `effect_name: str`), matching `custom_themes`' shape exactly — this let the two
  previously-duplicated matching loops collapse into one shared `_resolve_from(now, entries)`
  helper, called once for `custom_themes` and once for `BUILT_IN_THEMES`. Behaviour for built-in
  themes is unchanged; this is a pure refactor.
- `parse_window_date(s: str) -> Window` — parses `"MM-DD"` or `"MM-DD HH:MM"` into the same
  `Window` tuple shape `BUILT_IN_THEMES` uses. Used both by `config.py`'s pydantic validator (to
  fail fast on a malformed string) and by the startup loader that builds the runtime
  `custom_themes` list passed to `resolve_theme` — one parser, not two.

**`LedTheme` dataclass — corrected 2026-08-23**: gains `leds: list[dict] | None = None`, and is
now `frozen=True` — the `BUILT_IN_THEMES` refactor below has `resolve_theme` return the same
singleton `LedTheme` instance by reference on every matching call, so it must stay immutable to
avoid one caller's mutation corrupting state shared across scheduler cycles. Separately,
**`effect_name` changes meaning**: it's the theme's *semantic identity*, used for diffing in
`apply_led_state` (see below) — not necessarily the literal wire value. For a built-in, identity
and wire value are the same (`"halloween_mode"`). For a custom theme, `effect_name` is the
theme's own name from its YAML (e.g. `"peace"`), not the wire effect `"steady_array"` — the
original version of this spec set `effect_name="steady_array"` uniformly for every custom theme,
which would make every custom theme diff as identical to every other one (switching from `peace`
to `st_george` would look like "no change" and never get pushed). The wire-level distinction is
handled entirely inside `apply_led_state`, described next.

**`app/hypervolt/charger.py` — `apply_led_state` extended for `leds`**: gains a
`leds: list[dict] | None = None` parameter. Diffing still compares `effect_name` against
`_current_led_effect` (the semantic identity, unchanged from slice 1's design) — this alone now
correctly distinguishes custom themes from each other and from built-ins/no-effect, since
`effect_name` is always a unique identity per the `LedTheme` correction above. When a push is
needed, the *wire* effect name differs from the semantic identity only for custom themes: send
`"steady_array"` with the `leds` array if `leds is not None`, otherwise send `effect_name` (or
`"none"`) directly as before. `_current_led_effect` is still set to the semantic `effect_name`
(e.g. `"peace"`), never to the wire value.

**`app/config.py`**: new `CustomLedTheme(BaseModel)` with `effect: str`, `start: str`, `end: str`.
`start`/`end` are validated via a pydantic `field_validator` (reusing `led.py`'s
`parse_window_date` so there's one date-format parser, not two) against the `MM-DD` or
`MM-DD HH:MM` format at config-load time — a malformed date string is a config-authoring error in
the file the operator just edited, and fails loudly the same way every other `AppConfig` field
already does (see ADR 0007's contrast between core config's fail-fast validation and this
feature's own runtime graceful-degradation). `LedConfig` gains
`custom_themes: list[CustomLedTheme] = []`.

**Startup loading**: a new function — `load_custom_themes(entries: list[CustomLedTheme],
led_effects_dir: Path) -> list[tuple[LedTheme, Window, Window]]` in `led.py` — runs once at
startup (called from `main.py`, where `config_path` is already available to compute
`led_effects_dir`). For each `CustomLedTheme`, attempt `load_custom_effect(led_effects_dir /
f"{effect}.yaml")`; on any exception, log an error naming the effect and the exception, and drop
that entry; on success, build `(LedTheme(effect_name=entry.effect, leds=leds),
parse_window_date(entry.start), parse_window_date(entry.end))` and keep it. The resulting list
is passed to `ScheduleCoordinator` (a new constructor parameter, stored and threaded through to
every `resolve_theme(...)` call in `_apply_led_state()`) — resolved once, not re-loaded per
cycle. The app starts regardless of any individual failure, and every other configured theme,
extension, and built-in continues to work (ADR 0007). This is deliberately different from the
date-string case above: a bad *value the operator typed directly into config.yml* fails the
config load outright (matches the rest of `AppConfig`), whereas a bad *external YAML file* — which
might be a shipped file, might be edited independently of config.yml, and isn't itself part of
the validated config schema — degrades gracefully instead.

**`led_effects_dir` resolution**: `config_path.parent / "led_effects"` — no new CLI argument.
This mirrors `config.yml`'s own location exactly (both are declarative data an operator edits),
and in the deployed container resolves to `/config/led_effects` since `config.yml` already lives
at `/config/config.yml`.

**Shipped theme files** (`led_effects/`, five files): `peace.yaml`, `qe_ii.yaml`, `diana.yaml`,
`st_george.yaml`, `st_patricks.yaml`. Each is referenced from `config.yml.template` as a
commented-out example `custom_themes:` entry, not enabled by default.

**Dependency**: `pyyaml` (already a project dependency).

## Testing Decisions

**Re-planned 2026-08-23** for pytest, following the same seam split established in
`feature-led-brightness-and-builtin-themes.md`:

- **`load_custom_effect(path)`** — pure, no I/O beyond reading the given file — tested directly
  with real temp YAML files (`tmp_path`): a valid file produces the exact expected 51-element
  `leds` array; missing `default_colour`, an invalid hex value, and an out-of-range segment index
  each raise.
- **`parse_window_date(s)`** — pure string parsing — tested directly for both `"MM-DD"` and
  `"MM-DD HH:MM"` forms and for malformed input raising.
- **`resolve_theme`'s custom tier** — extends the existing pure-logic tests in `test_led.py`: a
  custom theme resolves during its window and falls through to built-ins outside it; a custom
  theme takes priority over a built-in matching the same date; `config.yml` list order decides
  the winner when two custom themes' windows overlap.
- **The `effect_name`-as-identity fix** — its own dedicated test at the `HypervoltChargerClient`
  seam (`test_charger.py`, mocking only `HypervoltWebSocketClient`): switching from one custom
  theme to another (both via `steady_array` on the wire) is detected as a change and pushes the
  new `leds` array, not silently skipped as "unchanged" the way the original `effect_name` design
  would have.
- **`load_custom_themes` failure handling** — tested directly: an entry pointing at a
  non-existent or malformed YAML file is dropped from the returned list (not raised), and other
  valid entries in the same call are still returned.
- **`CustomLedTheme` date-string validation** — tested like `Schedule`'s field validators in
  `test_config.py`: a malformed `start`/`end` string raises `ValidationError` at config-load time.

Manual verification remains worthwhile for the same reason as slice 1: physically observing the
LEDs once, as a wire-format sanity check that `steady_array` + a real `leds` array actually
produces the intended colours on hardware — not a substitute for the automated coverage above.

## Out of Scope

- Dynamic `LedThemeProvider` extensions — final slice, `feature/led-theme-extensions`.
- Any UI or CLI tooling for authoring theme YAML files — hand-written by the operator.
- Validating YAML colour data beyond what `load_custom_effect` needs to construct the `leds`
  array (e.g. no colour-contrast or accessibility checking).

## Further Notes

Depends on `feature/led-brightness-and-builtin-themes` having merged first — this slice extends
`resolve_theme`'s existing signature and `LedConfig`, it doesn't introduce either from scratch.
