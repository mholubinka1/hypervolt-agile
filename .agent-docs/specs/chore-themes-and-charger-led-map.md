# themes/ Directory and the Charger LED Map Reference

## Problem Statement

Two problems, both blocking clean theme-design work:

1. **Theme material is scattered and the location is awkward.** Custom-theme colour YAMLs are
   read from the *operator's* config directory (`<config dir>/led_effects/`), the repo ships none,
   and the reference implementation's Saints colour map has no natural home. There is no single
   place that is "the themes".

2. **There is no authoritative record of where the LEDs physically are.** The calibration export
   is unreliable for the lightning bolt, the geometry was then refined by hand across several
   on-charger Saints tests, and that refined layout currently lives only inside a throwaway
   preview page's JavaScript. Every future theme (`valentines`, `bonfire_night`, `pride`, a
   `saints_fc` restripe) needs one canonical map to design against, and that map must stay
   editable as more is learned about the charger.

## Solution

### A repo `themes/` directory

All custom-theme material lives in a single `themes/` directory in the repo. Custom-theme colour
YAMLs are resolved from there, not the operator's config directory. Shipped example maps and any
operator-added maps sit side by side; each stays **opt-in** — a colour map does nothing until a
`custom_themes` entry in `config.yml` references it with a date window (unchanged from today,
consistent with ADR 0009 for built-ins). This is a **hard break**: an operator with a
`<config dir>/led_effects/` directory must move those files into the repo's `themes/`; there is
no fallback and no deprecation shim.

### The Charger LED map — an editable reference

`themes/reference/charger_led_map.html` is a standalone page (opens from `file://`) showing the
Hypervolt Home 3 face at true scale (243 × 328 mm). All 51 LEDs are numbered, region-coloured
handles that can be **dragged** to reposition at true scale; indices 20–26 are drawn hollow
because they do not visibly light. **Export** downloads `charger_led_map.json`; **Load**
re-imports a prior export so the map can be iterated over time. Loading a `themes/*.yaml` colour
map recolours the handles and regenerates that YAML's segment ranges, so it doubles as the theme
previewer.

`themes/reference/charger_led_map.json` is the committed golden record — the single source of
truth for LED positions that every theme is designed against. It is seeded from the current,
on-charger-validated Saints geometry.

## User Stories

1. As a maintainer, I want every custom-theme colour map in one repo directory, so that "the
   themes" is a single place, not split between the repo and each operator's config directory.
2. As a maintainer, I want the Saints reference colour map to live in `themes/`, so that it sits
   with the other theme material rather than under `config/`.
3. As an operator, I want a shipped example theme to stay inert until I list it in `config.yml`,
   so that my config file remains a complete statement of what is active.
4. As an operator upgrading, I want the `led_effects/` → `themes/` change called out in the
   README and release notes, so that I know to move my colour files and why my themes stopped
   loading if I don't.
5. As a theme designer, I want one true-scale page showing exactly which LED index sits where,
   so that I can design a colour map without guessing positions.
6. As a theme designer, I want to drag an LED to a new position when I learn its real location,
   and export the corrected map, so that the reference improves over time instead of ossifying.
7. As a theme designer, I want indices 20–26 shown as dead, so that I don't waste effort
   colouring LEDs that never light.
8. As a theme designer, I want to load my draft `themes/*.yaml` onto the map and see its colours
   in place, and get its `segments` ranges back, so that authoring a theme is visual.
9. As a maintainer, I want `charger_led_map.json` covered by a test for its invariants, so that
   a hand-edit that breaks the schema (wrong count, out-of-bounds coordinate, a live 20–26) is
   caught in CI.
10. As a maintainer, I want the ad-hoc `show_led_theme.py` runner kept, pointed at `themes/`, so
    that I can push a theme YAML to the charger to eyeball it.

## Implementation Decisions

### `themes/` resolution

- `app/main.py` stops using `config_path.parent / "led_effects"`. The custom-theme directory is
  the repo's `themes/`, resolved relative to the application
  (`Path(__file__).resolve().parent.parent / "themes"`), and passed to
  `load_custom_themes_for_config`.
- `app/hypervolt/led.py`: `load_custom_themes_for_config(led_config, led_effects_dir)` and
  `load_custom_themes(entries, led_effects_dir)` take a parameter renamed to `themes_dir`;
  `load_custom_themes` resolves `themes_dir / f"{entry.effect}.yaml"`. `load_custom_effect` is
  unchanged (still takes a `Path`).
- Only `themes/*.yaml` at the top level are candidate colour maps; `themes/reference/` is a
  subdirectory the loader never scans (it globs one level, or the resolver only ever builds
  `themes/<name>.yaml` paths from `custom_themes` entries — the latter, since resolution is
  name-driven, not a directory scan).
- No `led_effects/` fallback path anywhere.
- `config/config.yml.template` comments and `README.md` (the `custom_themes` note and the Docker
  section) are updated: colour maps live in the repo `themes/` directory; the per-operator
  `led_effects/` mount is gone.
- `Dockerfile` runtime stage gains `COPY themes ./themes` so the maps are actually in the image —
  without it, `THEMES_DIR` (`/app/themes`) is absent in the container and every custom theme is
  silently skipped, since there is no `led_effects/` fallback.
- The reference `saints_fc.yaml` colour map (currently on `chore/led-map-geometry-research` at
  `config/led_effects/saints_fc.yaml`) is added at `themes/saints_fc.yaml`.

### `themes/reference/charger_led_map.json`

Index-keyed, all 51 entries:

```json
{
  "0":  { "x_mm": 210.0, "y_mm": 15.0,  "region": "ring-top",          "live": true },
  "8":  { "x_mm": 16.0,  "y_mm": 27.0,  "region": "ring-top-left-corner", "live": true },
  "20": { "x_mm": 55.1,  "y_mm": 313.0, "region": "ring-bottom",       "live": false },
  "39": { "x_mm": 80.0,  "y_mm": 244.0, "region": "bolt", "bolt_segment": "lower-blade", "live": true }
}
```

- Coordinates: millimetres from the charger body's top-left corner (body 243 × 328 mm).
- `region` ∈ `ring-top`, `ring-top-left-corner`, `ring-left`, `ring-bottom-left-corner`,
  `ring-bottom`, `ring-bottom-right-corner`, `ring-right`, `ring-top-right-corner`, `bolt`.
- `bolt_segment` present **iff** `region == "bolt"`, ∈ `lower-blade` (39–41), `hook` (42–44),
  `upper-blade` (45–50).
- `live` is `false` for indices 20–26, `true` for all others.
- Seed values from the Saints FC Strip page geometry (`e3fd0ba`
  `scripts/saints_fc_theme.html`): the ring from its `ringPoints()` (`lerp`-spaced top edge 0–7
  at y≈15, corners 8/38, sides 9–17 / 29–37, bottom 19–27 at y=313), the bolt from its
  `boltPoints()` twelve explicit points `[80,244] [78,185] [105,218] [92,188] [88,178] [95,164]
  [104,154] [114,142] [133,126] [144,116] [158,95] [172,74]`.

### `themes/reference/charger_led_map.html`

- Vanilla HTML + inline CSS + inline vanilla JS + SVG. No framework, no build, no network. Opens
  directly from `file://`.
- The 51 positions are inlined as `<script type="application/json" id="charger-led-map">` — the
  same content as `charger_led_map.json` — so the page needs no `fetch`.
- SVG `viewBox` frames the 243 × 328 body plus a small margin. Each LED is a `<g>` with a
  `<circle>` and a `<text>` label; dead LEDs (20–26) render hollow / dashed.
- Drag: pointer events on each handle; movement converts screen px → mm via the SVG's
  `getScreenCTM()`, clamps the result to the body rectangle, and updates the handle plus a live
  mm read-out. Region is not recomputed on drag (it is a stable label per index).
- **Export**: serialises the current handle positions into the `charger_led_map.json` shape and
  triggers a `Blob` download named `charger_led_map.json`.
- **Load**: `<input type="file">` reads a `charger_led_map.json` and reseeds every handle.
- **Theme preview**: a file input / paste box takes a `themes/*.yaml`, parses `default_colour` +
  `segments` (`indices` / `ranges`), and fills each handle with the resulting colour; a companion
  read-out shows the `segments` blocks regenerated from the currently-shown colours (so a
  designer can paint by dragging colour and copy the YAML back out). A tiny hand-rolled parser
  for this exact YAML subset is acceptable — no external library.
- Respects `prefers-reduced-motion`; keyboard focus visible on handles; drag also reachable via
  arrow keys on a focused handle (nudge 1 mm).

### Housekeeping

- `scripts/show_led_theme.py` is vendored from `e3fd0ba` with its theme-directory reference
  updated from `led_effects` to the repo `themes/` path.
- `scripts/led_map_geometry_review.html` and `scripts/saints_fc_theme.html` from `e3fd0ba` are
  **not** carried over — `charger_led_map.html` supersedes both.
- After this branch merges, `chore/led-map-geometry-research` is deleted (local; it was never
  pushed).

### ADRs

1. **Custom themes move to a repo `themes/` directory** — resolved relative to the app, not the
   operator's config directory; hard break from `<config dir>/led_effects/` with no fallback.
   Trade-off: one canonical, shippable location vs a one-time manual move for existing
   deployments and the loss of the bind-mounted drop-in workflow (ADR 0009 set the precedent for
   accepting a compat break in this area).
2. **The charger LED geometry is a hand-maintained JSON exported from a drag-editor page** — not
   the calibration tool's output, not generated from code. `themes/reference/charger_led_map.json`
   is the single source of truth; it is corrected by dragging on `charger_led_map.html` and
   re-exporting. Alternatives rejected: re-run `calibrate_leds.py` (bolt data proved unreliable),
   a Python generator (the geometry is empirical, not derivable).

## Testing Decisions

Test observable behaviour at the highest seam.

- **`tests/hypervolt/test_led_load_custom_themes_for_config.py` and
  `test_led_load_custom_themes.py`** — already drive `load_custom_themes_for_config` /
  `load_custom_themes` with a `tmp_path` directory. Update for the parameter rename and assert a
  `custom_themes` entry resolves `<themes_dir>/<effect>.yaml`. A missing file still logs and is
  skipped (unchanged).
- **New `tests/themes/test_charger_led_map.py`** — load `themes/reference/charger_led_map.json`
  and assert: exactly 51 entries keyed `"0"`–`"50"`; each has numeric `x_mm` in `[0, 243]` and
  `y_mm` in `[0, 328]`; `region` is one of the allowed nine; `bolt_segment` is present exactly
  when `region == "bolt"` and is one of the three; indices 20–26 have `live == false` and every
  other index `live == true`; no two `live` entries share the same `(x_mm, y_mm)`.
- **`tests/test_config.py`** — the `custom_themes` config shape is unchanged; a smoke check that
  a config with a `custom_themes` entry still loads.
- `themes/reference/charger_led_map.html` — manual smoke on delivery: open from `file://`, drag a
  handle and confirm the mm read-out, Export then Load the file and confirm positions
  round-trip, paste `saints_fc.yaml` and confirm the stripe colours land on the right indices.
  No automated JS test — consistent with the existing decision for `led_map.html`.

Prior art: `tests/hypervolt/test_led_load_custom_themes*.py`; `tests/hypervolt/test_led_load_custom_effect.py`.

## Out of Scope

- A `--themes-dir` CLI override / mounted themes directory. The decision is "repo `themes/` only";
  if operator-supplied themes without a rebuild become a requirement, that is a follow-up
  mirroring `--extensions-dir`.
- Moving `built_in_themes` or the `extensions/` directory — unchanged.
- Any change to `resolve_theme`, the priority stack, or the `custom_themes` / `built_in_themes`
  config schema.
- Designing any actual new theme (`valentines`, `pride`, `bonfire_night`, `saints_fc` restripe) —
  each is its own later cycle, done against this reference.
- The `always_on` gate and Saints match-window work — that is the paused
  `feature/led-theme-display-behaviour` branch, which rebases on this.
- Re-deriving or re-measuring the LED geometry — the seed values are taken as correct; the page
  exists so they can be corrected later, not as part of this work.

## Further Notes

- **Deployment impact**: today, Docker operators drop colour YAMLs into the bind-mounted
  `/home/pi/.config/hypervolt-agile/led_effects/` without rebuilding. After this change, colour
  maps are image content under `themes/`; an operator adding their own must fork and rebuild (or
  a future `--themes-dir` must land). The README Docker section must state this plainly.
- The paused `feature/led-theme-display-behaviour` branch's slice 3 (`main.py` injecting a
  `led_effects` dir into `load_extensions` so the Saints extension finds its colour file) is
  **simplified** by this work: with `themes/` repo-relative and known, the Saints extension
  resolves `themes/saints_fc.yaml` itself with no injection. Its spec/issues get adjusted during
  that branch's rebase.
- CONTEXT.md is updated on this branch: **Custom theme** now points at `themes/`; **Charger LED
  map** term added.
- `config/config.yml` holds live credentials and is gitignored — never read, echo, or commit it.
