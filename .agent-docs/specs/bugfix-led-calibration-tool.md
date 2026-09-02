# LED Calibration Tool Fixes

## Problem Statement

`scripts/calibrate_leds.py` and its companion `scripts/led_map.html` (added in the
`feature/led-calibration-tool` work) don't work in practice:

1. Running `calibrate_leds.py` against the real charger prints `Index N` to the terminal as
   expected, but no LED actually lights up on the physical unit. The script sets brightness to
   `1.0` once at startup, but the charger's LED ring stays at its normal configured brightness
   (50%) — the one-shot brightness push either isn't sticking, or something else (most likely the
   main scheduler, if it's running against the same charger at the same time) is overriding it.
2. `led_map.html`'s two-panel design — a read-only diagram plus a separate scrollable list of 51
   freeform-text rows — is more page than the job needs. The operator wants to work directly off
   the diagram: type the index they see lit straight into a small circle at its approximate
   physical position, without a second panel to cross-reference against.

## Solution

**Issue 1**: `calibrate_leds.py` starts reading the charger's own confirmation of the brightness
it just pushed, rather than firing brightness once and trusting it. If the charger doesn't confirm
`1.0`, the script warns loudly (without stopping) that something else is likely overriding LED
state — pointing at the main scheduler as the probable cause. It then re-asserts both brightness
and the current frame on every loop iteration (not just once at startup), so that once nothing
else is fighting it, the charger stays pinned at full brightness for the whole run. The frame is
also changed from a single lit LED to a **cumulative** run: index 0 lit, then 0–1, then 0–2, and
so on — each newly-added LED is the one whose index the terminal is announcing, and the
previously-lit ones stay on so the operator watches the run grow rather than chasing a single
moving dot. Wrapping past index 50 resets to a single lit LED at index 0.

**Issue 2**: `led_map.html` drops the rows panel entirely. The diagram becomes the whole tool:
redrawn to the Hypervolt Home 3's real body dimensions (328mm × 243mm, per the
[official technical spec](https://support.hypervolt.co.uk/en/knowledge-base/home-3-technical-sheet))
so LED positions are genuinely to scale, not an arbitrary invented layout. Each of the 51 LED
positions is a small circle holding a number input — the operator finds the dot at the position
they saw light up and types the index straight into it. The exported JSON records, per position,
the typed index and that position's true-scale x/y coordinates.

## User Stories

1. As the operator, I want the calibration script to warn me if the brightness I asked for isn't
   actually being applied, so that I know to check whether the main scheduler is running against
   the same charger before I waste a calibration pass.
2. As the operator, I want the script to keep re-asserting full brightness and the current frame
   throughout the run, so that once nothing else is fighting it, every LED I need to see is
   actually visible at full brightness.
3. As the operator, I want each step to add one more LED to a lit run that stays on, rather than
   move a single dot, so that I can see the progression build up and never lose track of where
   the sequence is.
4. As the operator, I want to type the index I observe directly into a circle at its approximate
   position on a true-to-scale diagram, so that building the map is a single, direct action
   instead of cross-referencing a diagram against a separate list.
5. As the operator, I want the exported record to include each position's real-world coordinates,
   so that the map is usable as a genuine scale reference, not just an index-to-guess lookup.
6. As the operator, I want a mistyped index (duplicate or out of the valid 0–50 range) to be
   flagged, not blocked, so that I can fix it later without the tool getting in my way mid-run.

## Implementation Decisions

**`scripts/calibrate_leds.py`**:

- Replace the current no-op `_ignore_state_update` callback with a real one that records the most
  recently reported `HypervoltChargerStateDelta.led_brightness` and sets an `asyncio.Event` when
  a value arrives (the delta carries `led_brightness` only when the charger's `sync.snapshot`
  response included a `brightness` key — see `HypervoltProtocol._on_sync_response`).
- After the first `set_led_brightness(1.0)` and first `set_led_effect("steady_array", ...)` push,
  **reset the tracker** (discard any reading), pause briefly to let the brightness write land,
  then call `ws_client.sync_charger_state()` (issues `sync.snapshot`) and wait briefly (a few
  seconds, bounded by a timeout) for the confirmation event. The reset matters: the websocket
  volunteers a `sync.snapshot` on connect, *before* this script has pushed anything, and without
  discarding it the check reads that stale pre-push brightness (observed as a false-positive
  warning on a genuinely-clean run). If the reported brightness isn't `1.0` once the wait
  resolves (or times out with nothing reported), print one warning identifying that the charger
  didn't confirm full brightness and that the main scheduler running against the same charger is
  the likely cause — then continue into the loop regardless. This check runs once, at startup,
  not on every iteration — the resend below is the actual mitigation, and repeating the read-back
  check every 10s would just spam the terminal without adding information.
- Inside the existing `0..50` loop, resend `set_led_brightness(1.0)` immediately before each
  `set_led_effect("steady_array", ...)` call, every iteration — not just once before the loop
  starts. Cheap (one extra small message per 10s hold), and guarantees full brightness for every
  frame whenever nothing else is actively overriding it.
- `_frame_for(index)` builds a **cumulative** frame: every LED from `0` to `index` inclusive is
  full white, the rest black — so the lit run grows one LED per step and previously-lit LEDs stay
  on. When the loop wraps past `50` back to `0`, the frame is just LED `0` lit, resetting the run.
  The terminal still prints the single newly-added `index` each step.
- No change to the connection setup, the Ctrl+C shutdown path, or the deliberate avoidance of
  `HypervoltChargerClient.create()` (still bypasses `clear_schedule()` — see the existing module
  docstring).

**`scripts/led_map.html`**:

- SVG `viewBox` changes from the current invented `440 640` (arbitrary units) to real millimetres
  matching the Hypervolt Home 3 body: `243` wide × `328` tall, plus a small margin for stroke
  width. The body `<rect>` and its corner radius are resized to these true dimensions.
- The ring and bolt guide `<path>`s keep the same structural split as today (ring = indices 0–38,
  39 points, anticlockwise from top-right; bolt = indices 39–50, 12 points, bottom to top,
  sampled via `getPointAtLength()` exactly as now) but are redrawn to sit within the true-scale
  body outline. The ring's inset from the case edge and the bolt's exact position/size within the
  body aren't in the datasheet, so those stay schematic best-guesses, same as the rest of the
  layout has always been — only the outer body dimensions are now sourced, not invented.
- Rendered large enough (diagram now has the full page width, no more 340px sidebar constraint)
  that true-scale spacing between the 51 points is enough on its own to fit 28px circles without
  overlap — no artificial exaggeration of spacing needed.
- Each of the 51 points becomes a `<circle>` (visual state: filled/highlighted once it holds a
  value) with a `<foreignObject>` positioned over it containing a real `<input type="number"
  min="0" max="50" step="1">`. Each input keeps a real, associated `<label>` (visually hidden)
  for accessibility, consistent with the recent accessibility work on this file (`3ab efabb`,
  `ceec472`) — no more relying on placeholder text or a separate visible `<label for>` in a rows
  list, since the rows list is gone.
- Out-of-range (outside 0–50) or duplicate (same index typed into more than one circle) values
  get a visual flag (red border on the input) — validation never blocks typing or clears a value.
- The rows panel, `.layout` two-column grid, `buildRows`, `focusRow`, `.row*` CSS, and the
  `.diagram-card` sticky-sidebar styling are removed entirely — the diagram is the only panel.
- `localStorage` key bumps to a new name (schema is now position-keyed, not index-keyed, so the
  old key must not silently half-apply under the new model) and the schema changes shape from
  `{index: "freeform text"}` to `{positionId: index}` internally, matching what's now typed
  directly into each circle. Position IDs follow the same traced order as today's indices:
  `ring-0`…`ring-38`, `bolt-0`…`bolt-11`.
- Export JSON changes shape to one entry per position: `{ "ring-0": {"index": 7, "x": 12.3, "y":
  4.1}, ..., "bolt-11": {"index": 44, "x": ..., "y": ...} }`, `x`/`y` in millimetres relative to
  the diagram's true-scale coordinate system. This supersedes the current `{index: label}` export
  shape — nothing downstream consumes the old file yet (theme design work is still blocked,
  follow-on work per the original spec), so no migration path is needed.

## Testing Decisions

- **`calibrate_leds.py`**: still no automated tests — this remains a connection-and-hardware-
  dependent manual tool, consistent with the existing decision recorded for `scripts/` (outside
  the `pyproject.toml` coverage gate, same convention as `scripts/volvo_auth.py`). **New for this
  implement loop specifically**: before this branch goes to code review, the operator runs the
  updated script against the real charger and confirms (a) LEDs are now visible, and (b) if the
  main scheduler happens to be running concurrently, the new warning fires as expected. This is a
  required manual gate, not optional smoke-testing — implementation of Issue 1 doesn't count as
  done until this confirmation happens.
- **`led_map.html`**: still no automated tests — no existing JS test tooling in this repo to
  stand up for one page, consistent with the existing decision. Manual smoke check on delivery:
  open in a browser, type into a few circles, refresh and confirm persistence, trigger a
  duplicate/out-of-range value and confirm the flag appears, click Export and confirm a valid
  JSON file downloads with the new per-position shape.

## Out of Scope

- Any actual theme design work using the resulting map — unchanged from the original spec, this
  tool produces the map, not the themes built from it.
- Automatically stopping or detecting-and-killing the main scheduler process from within
  `calibrate_leds.py` — the script warns; the operator decides what to do about it.
- Sub-millimetre precision on the ring inset or bolt geometry — those stay best-guess, only the
  outer body dimensions are now real.
- Any CLI configurability of batch size, hold duration, or the "on" colour — unchanged from the
  original spec.
- Automatically writing the exported JSON back into `led_map.html` itself — export-then-commit
  stays a manual step, unchanged from the original spec.

## Further Notes

- This is a fix to `feature/led-calibration-tool` (merged in PR #100), not a new tool — the ring/
  bolt index split, the Ctrl+C cleanup behaviour, and the overall script structure are unchanged
  except where called out above.
- Hypervolt Home 3 physical dimensions (328mm H × 243mm W × 101mm D) sourced from
  [Hypervolt's own technical spec sheet](https://support.hypervolt.co.uk/en/knowledge-base/home-3-technical-sheet).
