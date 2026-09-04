# LED brightness is binary and LED display is decoupled from plug state, gated per-theme by `always_on`

`led.brightness` let an operator dim the whole LED ring, and a charging charger with no theme
resolved still lit plain white at that brightness. ADR 0010 then made any resolved theme display
whenever a car was *plugged in*, charging or not. The result was three half-measures: a dimmed
ring nobody asked to dim, a resting state that was lit-but-white rather than off, and a single
plug-state gate applied identically to every theme with no per-theme say.

**The decision.** Brightness is now binary. A theme that displays does so at full brightness
(`1.0`); when nothing displays the ring is fully off (`0.0`). The `led.brightness` field is
removed from `LedConfig`, which now sets `extra="forbid"` — a `config.yml` still carrying
`brightness`, or any other unknown key under `led:`, fails at load rather than being silently
ignored (ADR 0007's fail-fast-on-config-values convention). The old "plain white while charging
with no theme" branch is deleted: no theme means the ring is dark, even mid-charge.

Each custom and built-in theme carries an `always_on: bool` config field, default `false`. It
is threaded onto the `LedTheme` the loaders build and preserved through `resolve_theme`'s
defensive copy. `false` (charging-gated) lights the charger only while a car is actively drawing
power; `true` (always-on) lights it for the theme's whole `start`–`end` window regardless of
charge state **and regardless of plug state** — an empty, unplugged charge point lights up.
`car_plugged` is no longer consulted for LED display anywhere. `_apply_led_state` collapses to:
if `led` is absent/disabled or `is_charging` is unknown, do nothing; resolve the theme; if a
theme resolved and (`theme.always_on` or the car is charging), push it at `1.0`; otherwise push
the off state.

**The alternative rejected.** Keeping a configurable brightness and a global plug-state gate,
and adding `always_on` only on top. Rejected because the dimming knob and the plain-white
resting state had no constituency — no user story wanted either — and carrying them forward
would keep `_apply_led_state` juggling a brightness value and a `car_plugged` read that now
mean nothing to the display decision. Making brightness binary and deleting the plug-state gate
lets the per-theme `always_on` flag be the *only* thing that decouples display from charging
state, which is exactly the control the operator asked for.

**Supersedes ADR 0010.** That ADR's "decouple from charging but still require the car plugged
in" floor is gone; the `always_on` flag now governs the charge-state decoupling on a per-theme
basis, and plug state gates nothing.
