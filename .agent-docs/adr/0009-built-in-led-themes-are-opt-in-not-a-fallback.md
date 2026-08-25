# Built-in LED themes are fully opt-in config; there is no implicit hardcoded fallback

`halloween_mode`, `christmas_mode`, and `party_mode` used to be an always-available lowest tier
in `resolve_theme`'s priority stack — hardcoded in `led.py`, active for every operator with no
config needed. They are now driven entirely by `led.built_in_themes` in `config.yml`: an effect
only fires if it has an explicit `start`/`end` entry there. No entry means it never runs, even
though the hardcoded window still exists in `led.py` as reference data.

The alternative considered was a partial-merge model — configuring one effect's window would
leave the other two on their hardcoded defaults, so an empty/absent `built_in_themes` list would
behave exactly as before. That was rejected: it would mean an operator's `config.yml` is no
longer a complete statement of what's active — knowing what's currently on would require
cross-referencing hardcoded source alongside the config file. Full opt-in was chosen so listing
an effect is unambiguously what enables it, matching how `custom_themes` already works, at the
cost of breaking implicit backward compatibility for any config that assumed the three built-ins
just worked without being listed.
