# Custom-theme colour maps move to a repo `themes/` directory

Custom-theme `*.yaml` colour maps are now read from a `themes/` directory at the repo root
(resolved relative to the application as `hypervolt.led.THEMES_DIR`), not from
`<config dir>/led_effects/`. The move gives every theme one canonical, version-controlled home
alongside the reference tooling (`themes/reference/`), lets the repo ship example maps, and keeps
`config.yml` the single statement of what is active (a map still does nothing until a
`custom_themes` entry names it — consistent with ADR 0009 for built-ins).

This is a **hard break**: there is no `led_effects/` fallback and no deprecation shim. An
operator with colour files in the bind-mounted `/home/pi/.config/hypervolt-agile/led_effects/`
must move them into the repo's `themes/` and rebuild the image; until they do, those
`custom_themes` entries log an error and are skipped. The alternative — reading both locations,
or a `--themes-dir` mount mirroring `--extensions-dir` — was rejected for now to keep resolution
to one place; a mounted override remains an easy follow-up if operator-supplied themes without a
rebuild becomes a real need. The cost is the one-time manual move and the loss of the drop-in
workflow; ADR 0009 already set the precedent for accepting a compatibility break in this area
for the sake of an unambiguous config surface.
