# Charger LED map — Save writes positions back in place

## Problem Statement

`themes/reference/charger_led_map.html` is the Charger LED map: the true-scale, drag-editable
reference every theme's colours are designed against (per ADR
0013-charger-led-geometry-is-a-hand-maintained-json). Today, correcting a position means dragging
a handle, clicking "Export JSON", finding the downloaded `charger_led_map.json` in the Downloads
folder, and manually moving it over `themes/reference/charger_led_map.json` — and separately,
hand-copying the same JSON into the page's own inline `<script id="charger-led-map">` seed, since
that embedded copy is what the page actually shows on next load (the page cannot `fetch` a sibling
file from `file://`). Skipping either step means the correction is lost the next time anyone —
including the same person, on a different branch or machine — opens the page: it silently reverts
to the last-committed layout. There is no single action that makes a drag-corrected layout durably
"saved."

## Solution

The "Export JSON" button is renamed "Save". On a Chromium browser (Chrome, Edge), clicking it
writes the corrected positions directly back to `themes/reference/charger_led_map.json` **and** a
regenerated `themes/reference/charger_led_map.html` (new positions baked into its own inline seed)
on disk, in place — no Downloads-folder detour, no manual file move, no hand-copy into the page's
own source. The first Save on a given browser profile prompts the user, via the browser's native
file picker, to pick each of the two existing files once (granting write access); every later Save
in that browser reuses the remembered access, only asking for a one-click permission re-grant per
page load. Once the two files are committed, anyone who opens the page — on any branch, browser,
or machine — sees the saved layout immediately, with no separate load step.

Firefox and Safari have no File System Access API, so there Save falls back to the previous
Blob-download behaviour (both files download; a status message explains they need moving into
`themes/reference/` by hand), so the page keeps working everywhere, just with the automation only
on Chromium.

## User Stories

1. As a theme designer, I want to drag an LED and click Save, so that the correction is written
   straight back to the committed reference files with no extra manual step.
2. As a theme designer using Chrome or Edge, I want my first Save to prompt me once to pick the
   existing `charger_led_map.json` and `charger_led_map.html`, so that subsequent Saves in that
   browser write silently (beyond a one-click permission re-grant per page load).
3. As a theme designer on Firefox or Safari, I want Save to still produce downloadable files with
   a clear message about what to do with them, so that the page remains usable without Chromium.
4. As a maintainer, I want the page's own embedded seed and `charger_led_map.json` to always agree
   after a Save, so that the existing schema test keeps passing and the page never silently shows
   a stale layout to the next person who opens it.
5. As a maintainer, I want the committed `charger_led_map.json` and the page's embedded copy to
   both update from the same Save action, so that "the golden record" (ADR
   0013-charger-led-geometry-is-a-hand-maintained-json) is never split across two out-of-sync
   files.
6. As a theme designer, I want Reset to seed and Load map… to keep working unchanged, so that I
   can still discard in-progress drags or load an older exported map for comparison.

## Implementation Decisions

- `themes/reference/charger_led_map.html`: the `#export` button's label changes from
  "Export JSON" to "Save"; its handler is replaced with a save routine (see below). `#load-map`
  and `#reset` are unchanged.
- **File handle acquisition and persistence**: on Save, if no remembered, permitted
  `FileSystemFileHandle` exists for a given target file (`charger_led_map.json`,
  `charger_led_map.html`), call `window.showSaveFilePicker` (suggested name matching the target)
  so the user selects the existing file; store the resulting handle in IndexedDB, keyed by target
  filename. On every Save, first re-verify (and if needed, re-request) permission on any
  IndexedDB-stored handle via the handle's permission API before writing, so a stale or
  since-revoked grant re-prompts rather than throwing.
- **Writing**: for each target file, `handle.createWritable()`, write the new content, close. Both
  writes happen from a single Save click; the two files are written sequentially (avoids browsers'
  "site is trying to download/save multiple files" friction, which applies to downloads but is
  good practice to mirror here for consistency of behaviour across supported/fallback paths).
- **`charger_led_map.json` content**: unchanged shape/generation — the existing per-index
  `{x_mm, y_mm, region, bolt_segment?, live}` serialisation already used by the current export
  routine.
- **`charger_led_map.html` content**: generated by capturing `document.documentElement.outerHTML`
  (prefixed with `<!doctype html>`) once, at script initialisation, before `buildHandles()` or any
  drag mutates the DOM — this captures the page's pristine, as-loaded structure regardless of
  later interaction (drags, theme-preview colouring). On Save, the currently-tracked in-memory
  model is serialised to the same JSON shape as `charger_led_map.json` and substituted, via a
  targeted string replace, into the captured template's
  `<script type="application/json" id="charger-led-map">…</script>` block. The result is written
  as the new `charger_led_map.html`.
- **Feature detection and fallback**: if `window.showSaveFilePicker` is undefined (Firefox,
  Safari), Save falls back to the prior Blob-download behaviour for both files, with a status
  message noting they must be moved into `themes/reference/` manually. A user cancelling the
  native picker (either file) aborts that Save with a status message; no partial write is left
  (each file's write only proceeds after its own handle is confirmed).
- **Status feedback**: the existing `readout`/`status` element(s) report the outcome — success
  ("Saved to charger_led_map.json and charger_led_map.html."), permission re-grant needed, user
  cancelled, or fallback-downloaded — so the designer always knows whether the write actually
  landed on disk.
- Intro copy (`p.sub`) updated to describe Save instead of Export.
- `.agent-docs/context.md`: "Charger LED map" glossary entry updated to describe Save writing in
  place (done during design — see the branch's `.agent-docs/context.md` diff).
- ADR 0018-charger-led-map-save-regenerates-the-html-via-captured-outerhtml records the mechanism
  and the two rejected alternatives (Blob-download-and-manually-move; a small local write-back
  server).

## Testing Decisions

- This is a pure browser-JS change (drag handling, File System Access API calls) with no Python
  surface and no prior automated JS test for this page (documented precedent in
  `.agent-docs/specs/chore-themes-and-charger-led-map.md`'s Testing Decisions).
- `tests/themes/test_charger_led_map.py` stays unchanged and continues to guard the schema and,
  specifically, `test_the_reference_page_embeds_the_same_map_as_the_json_file` — the invariant
  that Save's dual-write is designed to uphold by construction (both files are serialised from the
  same in-memory model in the same action).
- Manual smoke test on delivery (Chromium): open the page from `file://`, drag an LED, click Save,
  grant access to both files via the native picker, confirm both `themes/reference/*.json` and
  `*.html` are updated on disk; reload the page and confirm the new position shows without a
  manual Load; drag again and Save a second time, confirming only a one-click permission
  re-grant (no re-navigating the picker) is needed.
- Manual smoke test on delivery (Firefox or Safari): confirm Save downloads both files and the
  status message explains the manual move.
- Existing manual smoke items (drag read-out, Load round-trip, theme YAML preview) re-verified
  unaffected.

## Out of Scope

- Any new Python/tooling consumer of `charger_led_map.json` beyond the page's own theme-preview
  panel — none exists today and none is being added.
- A local write-back server as a cross-browser fallback — rejected in ADR
  0018-charger-led-map-save-regenerates-the-html-via-captured-outerhtml.
- Designing any actual theme (`valentines`, `bonfire_night`, `saints_fc` restripe, etc.) — each is
  its own later cycle against this reference, unchanged from the prior spec's scoping.
- Re-deriving or re-measuring the LED geometry itself.
- Any change to `Load map…`'s parsing/import logic or `Reset to seed`.

## Further Notes

- Builds directly on `.agent-docs/specs/chore-themes-and-charger-led-map.md` and ADR
  0013-charger-led-geometry-is-a-hand-maintained-json — this spec only changes how a correction
  gets persisted, not what is recorded or how the page previews themes.
- `config/config.yml` holds live credentials and is gitignored — never read, echo, or commit it
  (unaffected by this work, restated per repo convention).
