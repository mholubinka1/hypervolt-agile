# A resolved LED theme displays whenever the car is plugged in, not only while actively charging

The original LED brightness/theme spec (`feature-led-brightness-and-builtin-themes.md`)
deliberately made the off-while-not-charging behaviour non-configurable: brightness was 0.5 only
during an active charging session and 0.0 otherwise, with no exception for an active themed
window. `ScheduleCoordinator._apply_led_state` now resolves the theme every cycle regardless of
`is_charging`, and applies it at the configured brightness whenever one resolves and
`car_plugged is True` — charging state no longer gates a themed display, only plug state does.
When no theme resolves, the original charging-gated brightness/off behaviour is unchanged.

This reverses that earlier explicit design call. The alternative — leaving themes gated on
`is_charging`, as originally specified — was rejected because it makes a themed window
functionally almost decorative-only: for most operators, actual charging happens for a few hours
overnight during the cheapest price window, so a "Christmas week" theme would in practice only
ever be visible during that narrow overnight slot rather than for the week it's meant to mark.
Decoupling from charging (while still requiring the car plugged in, so an empty charger point
doesn't light up) makes the themed window mean what it says — visible for the whole window,
whenever there's a car present to show it on.
