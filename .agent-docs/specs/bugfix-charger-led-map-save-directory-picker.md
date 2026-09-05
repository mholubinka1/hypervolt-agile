# Charger LED map: fix first-time Save SecurityError

## Problem Statement

On `themes/reference/charger_led_map.html`, clicking **Save** for the very first time on a
fresh browser profile fails. `saveInPlace()` needs a file handle for both
`charger_led_map.json` and `charger_led_map.html`; when neither is cached yet, it acquires
them one after another, each via its own `window.showSaveFilePicker()` call. Chromium
requires transient user activation for each such call, and that activation is consumed as
soon as the first call is made — not when it resolves — so the second `showSaveFilePicker()`
call always throws a `SecurityError` ("Must be handling a user gesture to show a file
picker"), even though both calls originate from the same click.

The designer sees a confusing, unnamed failure ("Save failed: ...") on their very first Save.
Clicking Save again happens to work, because by then the JSON handle is already cached and
only one picker call is needed — but nothing tells the designer that's the fix, and the first
attempt looks broken.

## Solution

Replace the pair of per-file `showSaveFilePicker()` calls with a single
`showDirectoryPicker()` call that asks the designer to choose the `themes/reference/` folder
once. Both file handles are then derived from that one directory handle via
`getFileHandle(name, { create: true })`, which needs no further user activation. The first
Save now succeeds in one click, writing both files, exactly like every Save after it already
does.

## User Stories

1. As a designer using the charger LED map editor for the first time on a fresh browser
   profile, I want clicking Save to write both `charger_led_map.json` and
   `charger_led_map.html` in one click, so that I don't hit a confusing error before I've
   even made my first edit.
2. As a designer who already has a cached directory handle from a previous session, I want
   Save to keep working exactly as it does today (re-verifying permission, re-prompting only
   if it's been revoked), so that this fix doesn't regress the already-working repeat-save
   path.
3. As a designer, if I pick the wrong folder (not `themes/reference/`) when prompted, I want
   a clear error telling me so, so that I don't end up with a silently-cached handle to the
   wrong location that every future Save then writes into.

## Implementation Decisions

- **Single cached handle instead of two**: the IndexedDB `handles` object store now holds one
  `FileSystemDirectoryHandle` under a fixed key (e.g. `themes-reference-dir`) instead of two
  `FileSystemFileHandle`s keyed by filename. `idbGet`/`idbSet`/`idbDelete`/`idbTransaction`
  are unchanged — only what gets stored under which key changes.
- **Picking**: `pickAndStoreHandle(filename)` is replaced by a directory-scoped equivalent
  that calls `window.showDirectoryPicker({ mode: "readwrite" })` (granting readwrite
  immediately, same as `showSaveFilePicker` already does today) and stores the result.
- **Verifying the picked directory**: before caching, confirm the chosen directory actually
  contains `charger_led_map.json` (call `getFileHandle(MAP_JSON_FILENAME)` without
  `create: true`, so a missing file rejects instead of silently creating one) — mirrors the
  filename-verification pattern already shipped for the old per-file picker on PR #141. If
  the file isn't found, reject with a clear message ("expected the themes/reference folder,
  but charger_led_map.json isn't there — click Save again and pick the right folder") and do
  not cache the handle. Do not treat any other rejection from this check (e.g. a transient
  permission hiccup) as a wrong-folder signal — only a not-found result means the wrong
  folder was picked.
- **Permission re-verification**: `ensureWritePermission(handle)` already operates generically
  on any handle with `queryPermission`/`requestPermission({ mode: "readwrite" })` — it needs
  no change to work with a directory handle instead of a file handle.
- **Deriving file handles**: `saveInPlace()` now does one `getOrPickHandle()` call (for the
  directory), then two `directoryHandle.getFileHandle(name, { create: true })` calls (JSON,
  then HTML) — neither of which needs further activation, so both can run after the
  directory is secured without hitting the SecurityError.
- **Handles are secured before either file is written**: the same invariant from the
  original in-place-save design carries over unchanged — the directory handle (and its
  permission) is fully secured before any write starts, so a cancelled or denied directory
  picker still leaves neither file touched.
- **Write and error paths unchanged**: `writeHandle()`, `partialSaveError()`,
  `permissionDeniedError()`, and `reportSaveError()` all operate on `FileSystemFileHandle`s
  exactly as before — nothing about writing or naming which file failed needs to change,
  since both files are still written individually via their own derived file handle.
- **No migration needed**: the old two-file-handle IndexedDB entries become orphaned once
  this ships. This feature is not yet released to real end users (still on an unmerged
  branch), so no cleanup or migration path is needed — the new code simply never reads the
  old keys.

## Testing Decisions

- No new automated test seam. `tests/themes/test_charger_led_map.py` only asserts on the
  static content shape of the checked-in JSON/HTML and never exercised the save/picker JS
  logic — that gap predates this fix and was already flagged and deliberately deferred during
  the PR #141 review (recorded as an advisory finding, not blocking). The File System Access
  API also cannot be meaningfully exercised without a real user gesture and a live OS file
  dialog, so there is no practical unit-test seam for the picker/permission flow itself.
- Verification is manual, in a real Chromium browser: clear the `charger-led-map-handles`
  IndexedDB database (simulating a fresh profile), click Save, confirm exactly one directory
  picker appears, pick `themes/reference/`, and confirm both files are written with no error.
  Then click Save again and confirm no picker appears (cached handle, permission already
  granted).

## Out of Scope

- Standing up a browser test harness (e.g. Playwright) for this file — a larger, separate
  scope decision, not forced by this fix.
- Any change to the non-Chromium `saveViaDownload()` fallback path — unaffected by this bug,
  since it never uses `showSaveFilePicker`/`showDirectoryPicker`.
- Any change to the intro copy, error message wording, or other content addressed by the
  PR #141 Copilot review round — this spec only covers the directory-picker restructuring.

## Further Notes

This bug was discovered as a new finding during the PR #141 code-review validation pass
(fixing 3 unrelated Copilot comments on the same file) and was explicitly scoped out to its
own follow-up rather than folded into that PR. Because the underlying `saveInPlace()` code
only exists on the still-open `feature/charger-led-map-save-in-place` branch (not yet on
`main`), this branch is based on that branch's tip rather than `main` — the eventual PR
should target `feature/charger-led-map-save-in-place` as its base, or wait for PR #141 to
merge first.
