# Physical LED Index Calibration Tool

> **Partly superseded by [`bugfix-led-calibration-tool.md`](bugfix-led-calibration-tool.md).**
> That spec replaces the two-panel `led_map.html` layout described below with a single
> true-scale diagram of position-keyed number-input circles, changes the `localStorage` and
> export shapes from `{index: label}` to `{positionId: {index, x, y}}`, and fixes
> `calibrate_leds.py` to verify brightness read-back and light LEDs cumulatively.

## Problem Statement

Every custom LED pattern this project has ever designed — the five originally-shipped
`custom_themes` examples, and the `valentines`/`bonfire_night`/`pride`/`saints_fc` designs
sketched during the `feature/led-themes-configurable` work — was built against a best-guess LED
layout, not a measured one. The only source for "which physical position is LED index N" is a
diagram embedded as a YAML comment in an unofficial, third-party reverse-engineered integration:
it establishes the ring/bolt split (indices 0–38 outer ring, 39–50 lightning bolt) and 13
landmark positions, but the other 38 positions are interpolated by assuming even spacing — never
confirmed against real hardware, and nothing confirms the map even matches this charger's specific
hardware revision. Every custom colour pattern designed against it is a guess wearing the
appearance of precision.

## Solution

A one-off, manually-run diagnostic script connects to the real charger and lights its 51 LEDs one
at a time — full brightness, full white, ten seconds each, index printed to the terminal — looping
continuously until the operator has seen enough and hits Ctrl+C. Watching the physical unit, the
operator can now read off the true index-to-position mapping by eye instead of trusting an
interpolated guess.

A companion local HTML page shows the same ring/bolt shape as an interactive diagram — one
labelled slot per LED index — so the operator can type in what they observed as they go, with an
export step to turn that into the permanent, committed reference for designing every future theme
against.

## User Stories

1. As the operator, I want a script that lights one LED at a time with its index printed to the
   terminal, so that I can visually confirm the real physical position of every one of the 51
   LEDs on my charger.
2. As the operator, I want the script to loop continuously until I stop it myself, so that I'm
   not racing a fixed timer or re-running the script partway through if I need another look at an
   earlier index.
3. As the operator, I want connecting this script to never touch my charger's active charging
   schedule, so that a diagnostic tool doesn't have side effects on how my car actually charges.
4. As the operator, I want an interactive diagram where I can type in what I observed for each
   LED, so that I end up with a durable, structured record instead of a page of handwritten notes.

## Implementation Decisions

**`scripts/calibrate_leds.py`** (new, standalone — never imported by or invoked from `app/`):

- Loads `AppConfig` the same way `app/main.py` does (`ConfigLoader` against a `--config-file`
  argument, defaulting to `config/config.yml` for convenience as a manually-run local tool).
- Connects directly via `HypervoltRestClient.create(username, password)` followed by a bare
  `HypervoltWebSocketClient(charger=rest_client.charger, access_token_callback=
  rest_client.get_access_token, on_state_update=<no-op async callback>)`, started with `connect()`
  as a background task and awaited via `wait_until_connected(timeout=30)` — deliberately bypassing
  `HypervoltChargerClient.create()`, whose initialisation calls `clear_schedule()` (pushes an empty
  schedule to the charger) as a side effect. That behaviour is correct for the main scheduler,
  which immediately rebuilds and re-pushes a real schedule on its next cycle, but wrong for a
  diagnostic tool with no relationship to scheduling at all. The minimal path here only ever calls
  `set_led_brightness`/`set_led_effect`, so the state-update callback can be a no-op — nothing
  downstream reads `HypervoltChargerState` in this script.
- Pushes `set_led_brightness(1.0)` once at startup.
- Loops indices `0..50` continuously (wrapping back to `0` after `50`) until interrupted: builds a
  51-element `leds` array (all black `{"r": 0.0, "g": 0.0, "b": 0.0}` except the current index,
  full white `{"r": 1.0, "g": 1.0, "b": 1.0}`), calls `set_led_effect("steady_array", leds=...)`,
  prints the index to the terminal, then `asyncio.sleep(10)` before advancing.
- Catches `KeyboardInterrupt` (Ctrl+C): sends `set_led_effect("none")` to clear the display, then
  disconnects the websocket and closes the REST client before exiting — the charger is never left
  showing a stray single LED after the script ends.
- No CLI flags beyond `--config-file` — batch size, hold duration, and colour are fixed by this
  spec, not configurable, since this is a single-purpose tool built for one specific job.

**`scripts/led_map.html`** (new, standalone, committed to the repo):

- Reuses the ring/bolt SVG geometry already prototyped in this project's design conversation
  history (a rounded-rect body outline traced by a `<path>`, 39 points sampled along it via
  `getPointAtLength()` for the ring, a hand-authored zigzag `<path>` similarly sampled for the
  12-point bolt) — same physical layout assumption as the interpolated map (ring 0–38 anticlockwise
  from top-right, bolt 39–50 bottom to top), since that structural split (which indices belong to
  the ring vs the bolt) is not what's in doubt; only the exact position of each index within its
  region is.
- **Revised during implementation**: an adjacent `<input>` directly on each of the 51 dots turned
  out to be unreadable in practice — a Playwright screenshot during manual verification showed
  inputs overlapping along the ring's straight edges and badly crowding the bolt's tight zigzag,
  since 51 labelled positions don't have room to breathe on a diagram sized for reading, not data
  entry. Replaced with a two-panel layout: the diagram (dots and index numbers only, read-only)
  stays on the left as a spatial reference; a separate scrollable list of 51 rows (index + input)
  sits alongside it, matching the actual workflow better anyway (the script announces "Index N"
  one at a time — the operator finds row N, types, moves on). Clicking a dot scrolls to and
  focuses its row, keeping the two views connected without cramming input widgets onto the image
  itself.
- Every input's `input` event writes the full 51-entry label set to `localStorage` (keyed
  distinctly from any other page's storage) so a browser refresh never loses progress.
- On page load, any existing `localStorage` values are read back in and pre-fill the inputs.
- An "Export JSON" button serialises `{index: label}` for all 51 entries and triggers a real
  browser file download (this is a plain local file opened via `file://`, not a hosted Claude
  Artifact, so `<a download>`/Blob-based downloads work normally) — the operator saves the result
  as the permanent record, distinct from the page itself, and commits it to the repo.
- Wrapped in try/catch around every `localStorage` access, rendering correctly with empty labels
  if storage is unavailable or blocked, consistent with treating browser storage as a convenience
  rather than a durable store.

## Testing Decisions

No automated tests. `scripts/` is outside the coverage-diff gate (`pyproject.toml`'s
`[tool.coverage.run]` only sources `app` and `extensions`), matching the existing convention for
`scripts/volvo_auth.py` (also untested, also a manual one-off operational tool). Verification for
`calibrate_leds.py` is inherently manual — the entire point is a human watching real hardware.
`led_map.html` gets a one-time manual smoke check (open in a browser, type into a few inputs,
refresh, confirm they persist, click Export, confirm a valid JSON file downloads) rather than
automated coverage, since it has no server-side logic and no existing JS test tooling exists in
this repo to stand up for one page.

## Out of Scope

- Any actual theme design work (`valentines`, `bonfire_night`, `pride`, the `saints_fc` restripe)
  — this tool produces the map that unblocks that work (`FEATURES.md` Feature 23); using the map
  to design those patterns is separate, follow-on work.
- The six unconfirmed native presets (`FEATURES.md` Feature 21) — unrelated to this tool; those
  need a captured wire payload, not a physical position map.
- Any CLI configurability of batch size, hold duration, or the "on" colour — fixed per this spec.
- Automatically writing the exported JSON back into `led_map.html` itself, or any other mechanism
  that closes the loop without a manual step — the export-then-commit flow is deliberately simple
  and manual, matching the rest of this tool's one-off, hand-operated character.

## Further Notes

- This tool is intentionally excluded from the main scheduler's Docker image/entrypoint — it's
  run manually, ad hoc, against the real charger, by an operator standing in front of it.
- The exact real-world labels (e.g. clock positions, "top of bolt") are left to the operator's own
  vocabulary — the tool doesn't prescribe a labelling scheme, since the operator is the one who
  has to write and later read them back.
