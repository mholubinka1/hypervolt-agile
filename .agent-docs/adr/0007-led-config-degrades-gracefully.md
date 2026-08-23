# LED configuration degrades gracefully; core app configuration remains fail-fast

`AppConfig` (`app/config.py`) validates `octopus`, `hypervolt`, and `schedule` strictly via
pydantic — a bad API key or an out-of-range `total_charge_duration` raises at load time and the
app exits (`ConfigLoader._load_config`). LED Theme Control deliberately does not follow this
pattern for anything under the `led:` block.

An extension that fails to load (missing file, no valid provider class, `__init__` or `start()`
raising), or a `custom_themes` entry with an unparseable date string, or a missing/malformed
`led_effects/*.yaml` file, all log an error naming the broken entry and are then skipped —
that theme or extension is treated as absent from the priority stack, and the app starts and
keeps running regardless. Nothing under `led:` can ever prevent startup or crash a running app.

This is a deliberate asymmetry with the rest of `config.py`: LED theming is a cosmetic layer on
top of the app's actual job (scheduling cheap charging), and a broken theme or extension must
never take that down. The cost is that a config typo under `led:` is discovered by reading logs
rather than by the app refusing to start — acceptable here specifically because nothing under
`led:` can affect charging behaviour, only what the LEDs display.
