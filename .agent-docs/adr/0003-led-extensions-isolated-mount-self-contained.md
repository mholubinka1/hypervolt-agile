# LED extensions live on their own /extensions mount, each fully self-contained

`extensions/*.py` (executable `LedThemeProvider` implementations) and `led_effects/*.yaml`
(static colour data for custom calendar themes) are operator-supplied content, distinct from the
built-in effects hardcoded in `app/hypervolt/led.py`. Neither can live inside `app/`, since that
directory is baked into the Docker image at build time (`Dockerfile`'s `COPY app ./app`) and the
image is pulled centrally via Watchtower — an operator could never add their own extension or
theme without forking this repo and running their own image, defeating the "operator-defined"
premise of the feature.

`led_effects/*.yaml` is pure data with the same nature as `config.yml` itself, so it lives
alongside it: `config_path.parent / "led_effects"`, i.e. `/config/led_effects` in the deployed
container, resolved from the existing `--config-file` path with no new CLI argument needed.

`extensions/*.py` is executable code, not data, so it gets its own mount (`--extensions-dir`,
`/extensions` in `docker-compose.yml`) rather than sharing `/config` — keeping `/config` as
global app configuration only. Each extension entry's `config:` dict in `config.yml` is passed
to that extension alone; there is no shared or inherited configuration between extensions, and
none is read from anywhere outside an extension's own entry. Every extension must be able to
operate correctly from its own config block in total isolation.
