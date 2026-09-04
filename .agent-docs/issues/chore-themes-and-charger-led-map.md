> Work complete — PR ready to merge.

# Issues: chore-themes-and-charger-led-map

## 1. Vendor the Saints colour map and theme runner into themes/

**Blocked by**: None

**GitHub**: #109

**User stories**: 2, 10

### What to build

Bring two files from `chore/led-map-geometry-research` (`e3fd0ba`) onto this branch:

- `config/led_effects/saints_fc.yaml` → **`themes/saints_fc.yaml`** (new `themes/` directory).
  Byte-identical content: `default_colour: "#FFFFFF"` with red ranges
  `[1,2] [5,6] [21,22] [24,25] [39,40] [42,44] [48,50]`.
- `scripts/show_led_theme.py` → `scripts/show_led_theme.py`, with its theme-directory reference
  updated from `led_effects` to the repo `themes/` path (it currently resolves
  `<config parent>/led_effects/<effect>.yaml`; it should resolve `themes/<effect>.yaml` relative
  to the repo).

Do **not** carry `scripts/led_map_geometry_review.html` or `scripts/saints_fc_theme.html` — they
are superseded by the reference page (issue #4).

### Acceptance criteria

- [x] `themes/saints_fc.yaml` exists and `hypervolt.led.load_custom_effect` parses it to 51 LEDs
      with red at indices `1,2,5,6,21,22,24,25,39,40,42,43,44,48,49,50`.
- [x] `scripts/show_led_theme.py` resolves `themes/<effect>.yaml` (default effect `saints_fc`),
      not `led_effects/`.
- [x] No `config/led_effects/` directory is introduced on this branch.
- [x] Pre-commit passes on both files.
- [x] Existing test suite still passes (no app code touched yet).

---

## 2. Resolve custom themes from the repo themes/ directory

**Blocked by**: #109

**GitHub**: #110

**User stories**: 1, 3, 4

### What to build

Move custom-theme colour-map resolution off the operator's config directory and onto the repo's
`themes/` directory. Hard break — no `led_effects/` fallback.

- Expose the repo `themes/` directory as `hypervolt.led.THEMES_DIR`
  (`Path(__file__).resolve().parents[2] / "themes"` from `app/hypervolt/led.py`).
  `app/main.py`: replace `config_path.parent / "led_effects"` with `THEMES_DIR`, passed to
  `load_custom_themes_for_config`.
- `app/hypervolt/led.py`: rename the `led_effects_dir` parameter to `themes_dir` in
  `load_custom_themes` and `load_custom_themes_for_config`; `load_custom_themes` resolves
  `themes_dir / f"{entry.effect}.yaml"`. `load_custom_effect` is untouched. Resolution stays
  name-driven (built from `custom_themes` entries), so `themes/reference/` is never scanned.
- `config/config.yml.template`: update the `custom_themes` comment — colour maps live in the repo
  `themes/` directory; drop the `/home/pi/.config/hypervolt-agile/led_effects/` bind-mount note.
- `README.md`: update the `custom_themes` note and the Docker section — colour maps are now image
  content under `themes/`; there is no per-operator `led_effects/` mount. State that adding your
  own colour map means forking/rebuilding (until a `--themes-dir` override exists).
- Raises **ADR 1** — custom themes move to a repo `themes/` directory; hard break from
  `<config dir>/led_effects/`, no fallback.

### Acceptance criteria

- [x] Given a `custom_themes` entry `effect: saints_fc`, the app resolves and loads
      `themes/saints_fc.yaml`.
- [x] Given a `custom_themes` entry whose `<effect>.yaml` is absent from `themes/`, the entry is
      logged and skipped (behaviour unchanged, just the directory changed).
- [x] No code path reads a `led_effects/` directory.
- [x] `load_custom_themes` / `load_custom_themes_for_config` expose `themes_dir`; existing tests
      updated for the rename and still pass.
- [x] `config/config.yml.template` and `README.md` no longer reference `led_effects/`.
- [x] `tests/test_config.py` still loads a config carrying a `custom_themes` entry.
- [x] ADR 1 committed under `.agent-docs/adr/`.

---

## 3. The golden charger LED map JSON

**Blocked by**: None

**GitHub**: #111

**User stories**: 5, 7, 9

### What to build

`themes/reference/charger_led_map.json` — the committed source of truth for LED positions.

- Index-keyed, exactly 51 entries `"0"`–`"50"`. Each: `x_mm`, `y_mm` (millimetres from the
  charger body's top-left corner; body 243 × 328 mm), `region`, `live`. `bolt_segment` present
  **iff** `region == "bolt"`.
- `region` ∈ `ring-top`, `ring-top-left-corner`, `ring-left`, `ring-bottom-left-corner`,
  `ring-bottom`, `ring-bottom-right-corner`, `ring-right`, `ring-top-right-corner`, `bolt`.
- `bolt_segment` ∈ `lower-blade` (39–41), `hook` (42–44), `upper-blade` (45–50).
- `live` is `false` for indices 20–26, `true` for all others.
- Seed values from the Saints FC Strip page geometry (`e3fd0ba` `scripts/saints_fc_theme.html`):
  ring from `ringPoints()` (top edge 0–7 `lerp(210,33,k/7)` at y≈15; corners 8 `(16,27)` / 38
  `(227,27)`; left 9–17 at x=15 `lerp(50,280,k/8)`; bottom-left corner 18 `(16,301)`; bottom
  19–27 `lerp(33,210,k/8)` at y=313; bottom-right corner 28 `(227,301)`; right 29–37 at x=228
  `lerp(280,50,k/8)`), bolt from `boltPoints()` `[80,244] [78,185] [105,218] [92,188] [88,178]
  [95,164] [104,154] [114,142] [133,126] [144,116] [158,95] [172,74]` for indices 39–50.

New test `tests/themes/test_charger_led_map.py`.

### Acceptance criteria

- [x] `themes/reference/charger_led_map.json` exists and is valid JSON.
- [x] It has exactly 51 entries keyed `"0"`–`"50"` with no gaps.
- [x] Every entry has numeric `x_mm` in `[0, 243]` and `y_mm` in `[0, 328]`.
- [x] Every `region` is one of the allowed nine.
- [x] `bolt_segment` is present exactly when `region == "bolt"` and is one of the allowed three;
      indices 39–41 → `lower-blade`, 42–44 → `hook`, 45–50 → `upper-blade`.
- [x] Indices 20–26 have `live == false`; every other index has `live == true`.
- [x] No two `live` entries share the same `(x_mm, y_mm)`.
- [x] `tests/themes/test_charger_led_map.py` asserts all of the above and passes.

---

## 4. The editable charger LED map reference page

**Blocked by**: #111

**GitHub**: #112

**User stories**: 6, 8

### What to build

`themes/reference/charger_led_map.html` — a standalone page (opens from `file://`, no network, no
framework, no build) that is both the geometry editor and the theme previewer.

- Inlines the issue #3 JSON as `<script type="application/json" id="charger-led-map">`.
- SVG at true scale: body 243 × 328 mm plus a small margin. Each LED is a numbered handle
  coloured by region; indices 20–26 render hollow / dashed (dead).
- **Drag**: pointer events on a handle; screen px → mm via `getScreenCTM()`, clamped to the body
  rectangle; a live mm read-out for the dragged handle. `region` / `bolt_segment` are fixed per
  index and not recomputed. Arrow keys nudge a focused handle 1 mm; focus outline visible;
  `prefers-reduced-motion` respected.
- **Export**: serialise current positions to the `charger_led_map.json` shape and trigger a
  `Blob` download named `charger_led_map.json`.
- **Load**: `<input type="file">` reads a `charger_led_map.json` and reseeds every handle.
- **Theme preview**: a file input / paste box takes a `themes/*.yaml`, parses `default_colour` +
  `segments` (`indices` / `ranges`) with a tiny hand-rolled parser (no library), and fills each
  handle with the resulting colour. A companion read-out regenerates the `segments` blocks from
  the currently-shown colours.
- Raises **ADR 2** — the charger LED geometry is a hand-maintained JSON exported from this
  drag-editor page, not calibration output and not code-generated.

### Acceptance criteria

- [x] Opens from `file://` with no console errors; all 51 handles render at true scale, numbered,
      region-coloured; 20–26 visibly distinct (hollow).
- [x] Dragging a handle moves it, clamps at the body edge, and updates the mm read-out; a focused
      handle nudges 1 mm per arrow-key press.
- [x] Export downloads `charger_led_map.json`; Load of that file restores the exact positions
      (round-trip).
- [x] Loading `themes/saints_fc.yaml` colours indices `1,2,5,6,21,22,24,25,39,40,42,43,44,48,49,50`
      red and the rest white on the map.
- [x] The regenerated `segments` read-out for the shown Saints colours reproduces the
      `[1,2] [5,6] [21,22] [24,25] [39,40] [42,44] [48,50]` red ranges.
- [x] `scripts/led_map_geometry_review.html` / `scripts/saints_fc_theme.html` are absent from the
      branch.
- [x] ADR 2 committed.

---
