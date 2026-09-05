# Issues: feature/charger-led-map-save-in-place

## Save regenerates a self-consistent charger_led_map.html alongside the JSON

**GitHub issue**: #139

**Blocked by**: None

**User stories**: 4, 5

### What to build

On `themes/reference/charger_led_map.html`, rename the "Export JSON" button to "Save" and give it
a save routine that produces both target files' *content* correctly, still delivered as the
existing Blob-download behaviour for now:

- `charger_led_map.json`: unchanged serialisation (current export logic).
- `charger_led_map.html`: capture `document.documentElement.outerHTML` (prefixed with
  `<!doctype html>`) once, at script initialisation, before `buildHandles()` or any drag mutates
  the DOM. On Save, serialise the current in-memory model to the same JSON shape used for
  `charger_led_map.json` and substitute it, via a targeted string replace, into the captured
  template's `<script type="application/json" id="charger-led-map">…</script>` block. Download
  the result as `charger_led_map.html`.

Update the intro copy (`p.sub`) to describe Save instead of Export.

### Acceptance criteria

- [ ] The button reads "Save" (not "Export JSON").
- [ ] Clicking Save after dragging one or more LEDs downloads both `charger_led_map.json` and
      `charger_led_map.html`.
- [ ] The downloaded `charger_led_map.json` matches the current export shape
      (`{x_mm, y_mm, region, bolt_segment?, live}` per index).
- [ ] The downloaded `charger_led_map.html`, opened directly, shows the dragged positions on
      load with no further action (its embedded seed reflects the drag).
- [ ] Replacing both committed files in `themes/reference/` with the downloads and running
      `tests/themes/test_charger_led_map.py` passes, including
      `test_the_reference_page_embeds_the_same_map_as_the_json_file`.
- [ ] Dragging further, then clicking Reset to seed, then Save again produces files reflecting
      the seed (not the pre-reset drag) — the regenerated HTML always reflects current in-memory
      state at click time.
- [ ] A theme YAML previewed on the page beforehand (handle colours changed) does **not** appear
      in the downloaded `charger_led_map.html` — the captured template is the pristine,
      as-loaded page, unaffected by preview state.

---

## Save writes both files in place via the File System Access API, with a download fallback

**GitHub issue**: #140

**Blocked by**: #139

**User stories**: 1, 2, 3, 6

### What to build

Layer in-place writing on top of the previous slice's file-content generation. On a browser
exposing `window.showSaveFilePicker` (Chromium — Chrome, Edge): the first Save for a given target
file (`charger_led_map.json`, `charger_led_map.html`) opens the native save picker so the user
selects the existing file, granting write access; the resulting `FileSystemFileHandle` is stored
in IndexedDB keyed by target filename. Every later Save re-verifies (re-requesting if needed)
permission on the stored handle before writing via `createWritable()`, rather than re-opening the
picker. The two files are written sequentially from one Save click. Status feedback (existing
readout/status element) reports success, a needed permission re-grant, or user cancellation
distinctly, and no partial write is left if either file's handle/permission step is cancelled.

On a browser without `window.showSaveFilePicker` (Firefox, Safari), Save falls back to the
previous slice's Blob-download behaviour, with a status message explaining the two files need
moving into `themes/reference/` manually.

### Acceptance criteria

- [ ] Chromium, first Save: clicking Save opens the native file picker once per target file;
      after both are picked, `themes/reference/charger_led_map.json` and `.html` are updated on
      disk with the dragged positions, with no download to the Downloads folder.
- [ ] Chromium, reloading the page and Saving again: no re-navigation of the picker is required
      — at most a one-click native permission re-grant per handle — and the files update again.
- [ ] Chromium, cancelling the native picker on either file: the Save aborts with a status
      message; neither file is left partially written for that click.
- [ ] Firefox or Safari: clicking Save downloads both files (as in the previous slice) and shows
      a status message stating they must be moved into `themes/reference/` manually.
- [ ] `Load map…` and `Reset to seed` continue to work unchanged in both the in-place and
      fallback paths.
- [ ] Manual smoke (Chromium): drag → Save → confirm both files updated on disk → reload page →
      confirm new position shown with no manual Load step.

---
