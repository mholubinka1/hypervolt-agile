# "Save" on the charger LED map writes both reference files in place via the File System Access API

`themes/reference/charger_led_map.html`'s former "Export JSON" button is renamed "Save". Clicking
it writes the drag-corrected positions directly back to `themes/reference/charger_led_map.json`
**and** a regenerated `themes/reference/charger_led_map.html` (new positions baked into its inline
`<script id="charger-led-map">` seed), in place, so the next person opening the page — on any
branch or machine — sees the saved layout with no manual "Load map…" step. This restores
[[0013-charger-led-geometry-is-a-hand-maintained-json]]'s golden record without breaking its
`file://`-only, no-server constraint (`charger_led_map.html`'s own comment: fetching a sibling file
is blocked from `file://`).

Writing happens through the File System Access API (`window.showSaveFilePicker`,
`FileSystemFileHandle.createWritable`). The first Save on a given browser profile opens the
native picker for each file — the user must navigate to and select the existing
`charger_led_map.json` / `.html` once, since the page cannot address that path directly — after
which both `FileSystemFileHandle`s are stored in IndexedDB and reused on every later Save; each
new page load only needs a one-click native permission re-grant per handle (a browser security
requirement — the API never allows silent, unprompted disk writes), not re-navigating the picker.
The regenerated HTML content is produced the same way considered previously: capture
`document.documentElement.outerHTML` (plus `<!doctype html>`) once, at script start, before any
drag mutates the DOM, then string-replace only the JSON payload inside that captured markup.

Two alternatives were rejected. Downloading both files as Blobs to the Downloads folder (works in
every browser, no picker/permission flow) was rejected because it still leaves a manual
"move the downloaded files over themes/reference/" step — exactly the friction Save exists to
remove. A small local server the page POSTs to (works in every browser, no File System Access
support needed) was rejected as a bigger scope change that reverses the page's `file://`,
no-server property for a small reference tool, trading "open the file and drag" for "remember to
start a process first."

The accepted cost: the File System Access API has no Firefox or Safari implementation, so Save
only writes in place on Chromium browsers (Chrome, Edge). Elsewhere it falls back to the old
Blob-download behaviour with a status message explaining the files need moving into
`themes/reference/` manually. The regenerated HTML is also a browser-serialised re-render of the
original markup (attribute order/quoting normalised, comments preserved) rather than a
byte-identical copy, so replacing the committed file shows a full-file diff on save rather than a
minimal one — accepted because the file is a build artefact regenerated wholesale each save, not
hand-edited between saves.
