# Issues: feature-led-themes-configurable

> Work complete — PR ready to merge.

## Built-in themes become config-driven (#92)

**Blocked by**: None

**User stories**: 1, 3

### What to build

Built-in LED themes (`halloween_mode`, `christmas_mode`, `party_mode`) stop being an implicit
hardcoded fallback and become fully opt-in via a new `led.built_in_themes` config list, mirroring
how `led.custom_themes` already works. Factor `CustomLedTheme`'s shared `effect`/`start`/`end`
fields and window-validity validators out into a common base so the new `BuiltInLedTheme` model
(effect must be one of the three known built-in names, the inverse of `CustomLedTheme`'s
collision check) doesn't duplicate that logic. `resolve_theme` takes `built_in_themes` as an
explicit parameter defaulting to empty, rather than reading a module-level global. Wire the new
config through `app/main.py` and `ScheduleCoordinator` the same way `custom_themes` already
flows.

### Acceptance criteria

- [x] Given `led.built_in_themes` lists `christmas_mode` with a start/end window, when the LED theme is resolved during that window (car plugged in), then `christmas_mode` is applied
- [x] Given `led.built_in_themes` is empty or absent, when the LED theme is resolved during what would have been `halloween_mode`'s old hardcoded window, then no built-in theme is applied
- [x] Given `led.built_in_themes` lists an effect name that isn't one of the three known built-ins, when `config.yml` is loaded, then startup fails with a validation error naming the invalid effect
- [x] Given a `built_in_themes` entry has an invalid date (`"02-29"`, malformed format, or end-before-start with no year-wrap), when `config.yml` is loaded, then startup fails the same way an equivalent `custom_themes` mistake already does
- [x] Given the existing `custom_themes` validators (date format, 29 Feb, end-before-start, year-wrap), when run against the refactored shared base, then all existing `CustomLedTheme` test cases still pass unchanged

---

## LED display decouples from charging state (#93)

**Blocked by**: #92 (Built-in themes become config-driven)

**User stories**: 2

### What to build

`ScheduleCoordinator._apply_led_state` resolves the LED theme every cycle regardless of
`is_charging`, applying a resolved theme at the configured brightness whenever the car is plugged
in — not only while actively charging. When no theme resolves, the existing charging-gated
brightness/off behaviour (brightness while charging, off otherwise) is unchanged. This applies to
every theme source (built-in, custom, extension) uniformly; no new per-theme override is added in
this slice.

### Acceptance criteria

- [x] Given a theme resolves and the car is plugged in but not currently charging, when LED state is applied, then the theme's effect is shown at the configured brightness
- [x] Given a theme resolves but the car is not plugged in, when LED state is applied, then the theme is not shown and the existing charging-gated fallback applies instead
- [x] Given no theme resolves and the car is charging, when LED state is applied, then brightness is set with no effect, exactly as before this change
- [x] Given no theme resolves and the car is not charging, when LED state is applied, then LEDs are turned off, exactly as before this change

---

## Document built-in theme configuration (#94)

**Blocked by**: #93 (LED display decouples from charging state)

**User stories**: 1

### What to build

Document the new `built_in_themes` config in both `config/config.yml.template` (a commented
worked example listing all three effects at their current default windows, with a note that
listing an effect is what enables it — no change to the existing `custom_themes` placeholder,
which stays example-free in this slice) and a new `README.md` subsection under `## Configuration`
with a runnable `led:` example.

### Acceptance criteria

- [x] Given a new operator reads `config/config.yml.template`, when they look at the commented `led:` block, then they see a worked `built_in_themes` example for all three effects with their default windows
- [x] Given a new operator reads `README.md`'s Configuration section, when they look for LED theme setup, then they find a runnable example `led:` block enabling the three built-in themes

---
