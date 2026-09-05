# Issues: bugfix-charger-led-map-save-directory-picker

> Implementation and code review complete. No automated test seam exists for this file's
> save/picker logic (agreed in advance — the File System Access API can't be meaningfully
> unit-tested without a real user gesture and OS dialog), so these criteria are verified by
> code-review tracing, not execution. Manual browser verification against the 6 scenarios
> below is still owed before merge — see the steps shared with the user.

## Fix first-time Save SecurityError via a single directory-picker handle (#142)

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

Replace the two sequential per-file `showSaveFilePicker()` calls in `saveInPlace()` with a
single `showDirectoryPicker({ mode: "readwrite" })` call that asks the designer to pick the
`themes/reference/` folder once. Cache that one directory handle in IndexedDB (replacing the
two per-file handle entries). Before caching, verify the picked directory actually contains
`charger_led_map.json`, rejecting with a clear message if it doesn't. Derive both the JSON
and HTML file handles from the cached directory handle via `getFileHandle(name, { create:
true })` — no further user activation needed — then write both files exactly as today.

### Acceptance criteria

- [x] On a fresh browser profile (no cached handle), clicking Save shows exactly one native
      picker (a directory picker, not a file-save picker), and after the designer picks
      `themes/reference/`, both `charger_led_map.json` and `charger_led_map.html` are written
      successfully with no error.
- [x] With a directory handle already cached and write permission still granted, clicking
      Save shows no picker at all and both files write silently, exactly as the current
      cached-handle Save flow does today.
- [x] If write permission on the cached directory handle has been revoked, Save re-prompts
      with the directory picker (not a per-file picker) and, once granted, re-caches the new
      handle the same way as the first-time flow.
- [x] If the designer picks a folder that does not contain `charger_led_map.json`, Save fails
      with a clear message naming the problem (wrong folder), the handle is not cached, and
      neither file is written.
- [x] If the JSON file writes successfully but the HTML file then fails (e.g. permission
      revoked mid-save), the existing partial-save error message still names which file
      landed and which failed — unchanged behaviour, now running on file handles derived from
      the directory handle instead of independently cached ones.
- [x] The non-Chromium `saveViaDownload()` fallback path is untouched and continues to work
      exactly as before (it never calls `showSaveFilePicker`/`showDirectoryPicker`).

---
