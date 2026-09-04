# hypervolt-agile

A scheduler that reads half-hourly Octopus Agile electricity prices and pushes a live charging schedule to a Hypervolt EV charger, so the car charges during the cheapest windows.

## Language

**Agile prices**:
Half-hourly electricity unit prices published by Octopus Energy for the Agile tariff; the app fetches these as the raw input to schedule building.
_Avoid_: tariff rates, unit rates

**Price** (`Price`):
A single half-hourly price period from Octopus, with a value excluding VAT and a validity window (`valid_from`/`valid_to`).
_Avoid_: rate, tariff slot

**Charging period**:
One half-hourly slot counted toward the configured total charge duration; `ScheduleBuilder` converts a duration in hours into a number of charging periods.
_Avoid_: time slot, block

**Charge session** (`ChargeSession` / `HypervoltSession`):
A contiguous window of one or more merged charging periods, with a start/end time and an average price per kWh, that gets pushed to the charger as part of a schedule.
_Avoid_: window, slot, charge window

**Schedule**:
The ordered set of charge sessions currently pushed to the charger; rebuilt each update cycle from the latest Agile prices.
_Avoid_: plan, timetable

**Poll cycle**:
One iteration of the scheduler's main loop (`poll_every_secs`), which checks charger/session state and re-pushes the schedule if needed; distinct from the less-frequent price update cycle (`update_every_mins`).
_Avoid_: tick, loop iteration

**Session clock offset**:
A small buffer trimmed from each charge session's start/end (`SESSION_CLOCK_OFFSET_MINS`) to account for clock drift between the app and the charger.
_Avoid_: buffer, margin

## Charger State

**Lock status** (`LockStatus`: `unlocked`, `pending_lock`, `locked`):
Whether the charger currently permits charging; the scheduler locks the charger outside scheduled windows and unlocks it during an active session.
_Avoid_: charger status

**Charging mode** (`ChargingMode`: `boost`, `eco`, `super_eco`):
The charger's native power-delivery mode, independent of scheduling.

**Activation mode** (`ActivationMode`: `plug_and_charge`, `schedule`, `octopus`):
The charger's source of charging instructions; this app operates by driving the charger while it's in `octopus`/`schedule`-compatible mode.

**Release state** (`ReleaseState`: `DEFAULT`, `RELEASED`):
Tracks user cancellation — when a user stops a charge via the Hypervolt app, the charger enters `RELEASED` and the scheduler holds back from re-locking until the car is re-plugged.
_Avoid_: cancelled, override

**Car plugged**:
Whether a vehicle is currently connected to the charger; combined with release state to decide whether the scheduler should act.

## LED Theme Control

**LED Theme**:
The resolved outcome for "what should the LEDs show right now" — an LED Effect to display, or nothing — chosen once per poll cycle by walking the priority stack (primary pass then fallback pass). A displayed theme is always shown at full brightness (1.0); when nothing displays the LEDs are fully off (0.0) — there is no dimmed state and no plain-white-while-charging state. Whether a resolved theme displays depends on its display gate (see Always-on theme).
_Avoid_: LED state, lighting mode, brightness

**LED Effect**:
The visual pattern applied to the charger's LEDs: either a built-in effect the charger firmware already knows how to render by name (e.g. `halloween_mode`), or a `steady_array` — an explicit 51-value RGB array the app constructs and sends itself.
_Avoid_: LED pattern, colour scheme

**Custom theme**:
An LED Effect backed by a static colour YAML file in the repo's `themes/` directory, mapped to a year-agnostic date window via a `custom_themes` entry in `config.yml`. Shipped maps and operator-added maps sit together in `themes/`; each is opt-in — listing it in `config.yml` is what activates it. Pure data — no logic.
_Avoid_: custom effect, theme file, led_effects

**LED Theme Extension**:
An operator-registered Python module implementing the `LedThemeProvider` protocol, resolving a theme dynamically (e.g. from a sports fixtures API) rather than from a fixed calendar window. Each extension operates from its own isolated config, with no shared mutable state and no coupling between extensions; a _shipped_ extension may read a repo asset (such as a `themes/` colour map) through `hypervolt.led`'s public API.
_Avoid_: LED plugin, dynamic theme

**Charger LED map**:
The canonical record of where each of the 51 LEDs sits on the charger face — millimetres from the body's top-left corner (body 243 × 328 mm), a region, and whether the LED visibly lights. Maintained by dragging LEDs at true scale on `themes/reference/charger_led_map.html`, then Saving, which writes the corrected positions straight back to the committed `charger_led_map.json` and a regenerated `charger_led_map.html` in place (Chromium only, via the File System Access API); every theme's colours are designed against it. Indices 20–26 are recorded but do not light.
_Avoid_: LED layout, position file, calibration map, exporting

**Priority stack**:
The authority order used to resolve which LED Theme applies when multiple sources could match at once: registered extensions (config list order) beat custom themes (config list order) beat built-in presets. Resolution runs a second, fallback pass — extensions' `resolve_fallback` — only when the first pass finds nothing, which is how the Saints FC extension sits _above_ everything inside its Match window and _below_ everything outside it.
_Avoid_: resolution order, precedence

**Always-on theme**:
A resolved LED Theme whose display gate is "display for the whole active window regardless of charge or plug state" — an empty, unplugged charge point still lights up (always at full brightness). The opposite is a charging-gated theme, which displays only while a car is actively charging and is otherwise fully off. Custom and built-in themes carry an `always_on` flag (default false — charging-gated) choosing between the two for their whole date window; the Saints FC extension is always-on within its Match window and charging-gated outside it. Plug state gates nothing — `car_plugged` is not consulted for LED display.
_Avoid_: persistent theme, forced theme

**Match window**:
The interval a Saints FC match-day theme outranks all other themes: 30 minutes before kick-off until three hours after, unioned across every Southampton fixture that local date (a rare double-header covers both). A fixture whose kick-off time is still unknown contributes no Match window — it is only a charging-gated fallback that day; kick-off times are polled hourly so an unknown kick-off is rare. Outside the window on a match day the strip becomes a charging-gated fallback below every custom and built-in theme; off a match day the extension contributes nothing.
_Avoid_: fixture window, game window

## Boundaries

**Octopus client**:
Talks to the Octopus Energy API to fetch Agile prices and resolve the account's timezone from postcode.

**Hypervolt client**:
Talks to the Hypervolt charger over REST and WebSocket to read state and push schedules/lock commands.

**Charger-local timezone**:
The timezone the charger operates in, derived from the Octopus account postcode; prices and schedules are held in UTC internally and converted to this timezone only when pushed to the charger or formatted for display.
_Avoid_: local time
