# Issues: bugfix/led-calibration-tool

## Fix calibrate_leds.py LED visibility (#106)

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

`calibrate_leds.py` currently pushes brightness `1.0` once at startup and trusts it, and lights a
single LED at a time. Replace the no-op state-update callback with a real one that records the
charger's confirmed LED brightness. After the first frame is pushed, discard the connect-time
snapshot, let the write settle, then trigger a `sync.snapshot` and wait briefly for confirmation;
if the charger doesn't confirm full brightness, print one warning identifying the main scheduler
(if running concurrently against the same charger) as the likely cause, then continue regardless.
Inside the existing 0–50 loop, resend both brightness and the current frame on every iteration,
not just once before the loop starts, so full brightness holds for the whole run once nothing
else is overriding it. Change the frame to a cumulative run — every LED from `0` to the current
index stays lit, so the operator watches the run grow rather than chase a moving dot; wrapping
past `50` resets to a single lit LED at `0`. No change to connection setup or the Ctrl+C shutdown
path.

### Acceptance criteria

- [ ] A real `on_state_update` callback captures the charger's reported LED brightness.
- [ ] After the first frame push, the tracker is reset (discarding the connect-time snapshot),
      then the script triggers a state sync and waits (bounded by a timeout) for the charger's
      confirmation — a genuinely-clean run produces no warning.
- [ ] If confirmed brightness isn't `1.0`, exactly one warning prints, naming the likely cause
      (concurrent scheduler); the script does not exit.
- [ ] Every loop iteration resends `set_led_brightness(1.0)` immediately before
      `set_led_effect("steady_array", ...)` for the current index.
- [ ] Each step lights every LED from index `0` to the current index inclusive; previously-lit
      LEDs stay on until the loop wraps past `50`.
- [ ] Ctrl+C still clears the display and disconnects cleanly (unchanged regression check).
- [ ] Manually verified against the real charger before this branch goes to code review: LEDs
      light progressively and stay lit, at full brightness, independently confirmed by the
      operator standing at the charger.

---

## Simplify led_map.html to a true-scale, position-keyed diagram (#107)

**Blocked by**: None

**User stories**: 4, 5, 6

### What to build

Replace the current two-panel layout (diagram + separate 51-row list) with a single true-scale
diagram. The SVG viewBox is resized to the Hypervolt Home 3's real body dimensions (243mm ×
328mm), with the ring/bolt guide paths and their 51 sampled points redrawn within that true
outline (ring inset and bolt position stay schematic, as today — only the outer body dimensions
are now sourced from the datasheet rather than invented). Each of the 51 points becomes a small
circle with an embedded number input (0–50) for the index the operator observed there, each with
a real accessible label. Out-of-range or duplicate values get a visual flag, never a block.
`localStorage` moves to a new position-keyed schema under a new key (old index-keyed data isn't
migrated). Export JSON changes shape to one entry per position, each carrying its typed index and
true-scale x/y coordinates in millimetres. The rows panel, its supporting JS, and its CSS are
removed entirely.

### Acceptance criteria

- [x] SVG viewBox and body outline reflect the Hypervolt Home 3's real 243×328mm dimensions.
- [x] All 51 LED positions render as a circle with an embedded, accessibly-labelled number input,
      directly on the diagram — no separate rows panel.
- [x] A duplicate or out-of-range (outside 0–50) typed value is visually flagged without blocking
      input or clearing the value.
- [x] Typed values persist to `localStorage` under a new position-keyed schema and repopulate on
      page refresh.
- [x] Export produces a JSON file with one entry per position, each including its typed index and
      x/y coordinates in millimetres.
- [ ] Manual smoke check performed: type into a few circles, refresh and confirm persistence,
      trigger a duplicate/out-of-range flag, export and confirm valid JSON in the new shape.

---
