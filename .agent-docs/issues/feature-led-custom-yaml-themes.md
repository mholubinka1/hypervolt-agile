# Issues: feature-led-custom-yaml-themes

> Work complete — PR ready to merge.

## Load and apply a custom YAML theme during its date window — [#82](https://github.com/mholubinka1/hypervolt-agile/issues/82)

**Blocked by**: None

**User stories**: 1

### What to build

Operators can define a static 51-LED colour pattern in a `led_effects/*.yaml` file and map it to
a year-agnostic date window via a `custom_themes:` entry in `config.yml`. A pure YAML parser
converts the file's colours into the charger's 51-element `leds` array, raising on missing file
or malformed content. `resolve_theme`'s middle (custom) tier is populated: each custom theme is
checked against the current date in config order, with priority over built-ins. `LedTheme` gains
a `leds` field, and its `effect_name` becomes the theme's semantic identity (its own name, e.g.
`"peace"`) rather than the wire value — this matters because the wire effect for every custom
theme is the same generic `"steady_array"`, so identity has to live somewhere else or switching
between two custom themes would look like no change and never get pushed.
`HypervoltChargerClient.apply_led_state` is extended to send the `leds` array alongside
`"steady_array"` on the wire whenever `leds` is provided, while still diffing on the semantic
`effect_name`. Custom theme config entries with a malformed date string fail config load outright
(same as any other bad `AppConfig` value); a custom theme entry whose YAML file is missing or
malformed is logged and dropped at startup, without affecting the app or any other theme.
Everything is loaded once at startup and threaded through `ScheduleCoordinator` rather than
re-read every cycle.

### Acceptance criteria

- [x] Given a `custom_themes` entry whose date window matches `now` and `is_charging` is `True`,
      when the scheduler runs, then the wire `effect_name: "steady_array"` and the theme's exact
      `leds` array (as produced by `load_custom_effect`) are sent — internally the theme is
      identified by its own name (e.g. `"peace"`), only the wire value sent to the charger is
      `"steady_array"` (**corrected 2026-08-24**, was stated as if `effect_name` itself were
      `"steady_array"`, contradicting the identity fix)
- [x] Given a custom theme and a built-in both match the same date, when the scheduler runs, then
      the custom theme takes priority
- [x] Given `now` matches no custom theme, when the scheduler runs, then built-ins are resolved as
      before (no regression to slice 1 behaviour)
- [x] Given the currently-active theme changes from one custom theme to a different custom theme
      (both wire as `"steady_array"`), when the scheduler runs, then the new `leds` array is sent
      — this is not silently treated as "unchanged"
- [x] Given the currently-active theme is unchanged between cycles, when the scheduler runs, then
      no redundant LED message is sent
- [x] Given a `custom_themes` entry's YAML file does not exist or is malformed, when the app
      starts, then it starts successfully, an error is logged naming the effect, and every other
      configured theme/built-in still resolves correctly
- [x] Given a `custom_themes` entry has a malformed `start`/`end` date string directly in
      `config.yml`, when the app starts, then it fails to start with a clear validation error,
      the same as any other bad `AppConfig` value
- [x] Given a `custom_themes` entry has `02-29` as its `start`/`end`, when the app starts, then it
      fails to start with a clear validation error (**added 2026-08-24**, caught by review —
      would otherwise crash `resolve_theme` on any non-leap year)
- [x] Given a `custom_themes` entry has a same-month `start`/`end` pair where `end` is
      chronologically before `start` (e.g. `start="03-16"`, `end="03-14"`), when the app starts,
      then it fails to start with a clear validation error (**added 2026-08-24**, caught by
      Copilot review round 2 — would otherwise silently produce a window that could never match
      any date)
- [x] `load_custom_themes` is called once at startup (not per scheduler cycle)

---

## Ship five ready-made custom theme files — [#83](https://github.com/mholubinka1/hypervolt-agile/issues/83)

**Blocked by**: [#82](https://github.com/mholubinka1/hypervolt-agile/issues/82)

**User stories**: 1

### What to build

Five ready-made `led_effects/*.yaml` files ship with the app as useful defaults and worked
examples for operators writing their own: `peace` (Ukraine flag colours), `qe_ii` (Queen
Elizabeth II anniversary), `diana` (Diana anniversary), `st_george` (St George's Day), and
`st_patricks` (St Patrick's Day). Each is documented in `config.yml.template` as a commented-out
example `custom_themes:` entry, not enabled by default.

### Acceptance criteria

- [x] All five `led_effects/*.yaml` files exist, each parses successfully via
      `load_custom_effect`, and each produces a visually sensible 51-element `leds` array for its
      theme
- [x] `config.yml.template` documents `custom_themes:` with all five files as commented-out
      example entries

---
