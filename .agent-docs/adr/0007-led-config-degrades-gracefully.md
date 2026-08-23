# External LED resources degrade gracefully; the led: config block itself stays fail-fast like the rest of AppConfig

**Corrected 2026-08-23** (Copilot review, PR #81): the boundary this ADR draws is not "anything
under the `led:` block" — it's "values declared directly in `config.yml`" versus "external
resources that config only references." `LedConfig` is a normal part of the `AppConfig` pydantic
tree (same as `octopus`, `hypervolt`, `schedule`): `enabled: bool` and `brightness: float =
Field(0.5, gt=0, le=1)` are both validated the ordinary way, and an out-of-range `brightness`
raises at load time and exits the app via `ConfigLoader._load_config`, exactly like a bad
`total_charge_duration`. The same will hold for `custom_themes`' `start`/`end` date-window
strings once that slice lands — a malformed one is a typo in the file the operator just edited,
and fails loudly the same way every other `AppConfig` field does (see
`feature-led-custom-yaml-themes.md`'s Implementation Decisions).

What actually degrades gracefully is **external resources that `led:` config merely points at**,
not the config values themselves: a `custom_themes` entry's `led_effects/*.yaml` file being
missing or malformed, and an extension's `.py` file failing to load (missing file, no valid
provider class, `__init__`/`start()` raising). Both log an error naming the broken entry and are
then skipped — that theme or extension is treated as absent from the priority stack — because,
unlike a config value, there's no way to validate an external file's *content* until the app
actually tries to use it, and by then refusing to start over one broken file would take down
LED theming entirely for one typo in a single custom effect or extension.

This is a deliberate asymmetry: LED theming is a cosmetic layer on top of the app's actual job
(scheduling cheap charging), and a single broken external file must never take that down. The
cost is that a broken YAML/extension file is discovered by reading logs rather than by the app
refusing to start — acceptable specifically because none of it can affect charging behaviour,
only what the LEDs display. A config-authoring mistake in `config.yml` itself, by contrast, is
caught immediately at startup like every other config field, so an operator gets the same fast
feedback loop they already have for the rest of the file.
