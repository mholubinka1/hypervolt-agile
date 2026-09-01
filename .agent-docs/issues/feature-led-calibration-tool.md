# Issues: feature-led-calibration-tool

> Work complete — PR ready to merge.

## Terminal calibration script (#98)

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

`scripts/calibrate_leds.py` — a standalone script that connects to the real charger via a minimal
LED-only path (bypassing `HypervoltChargerClient.create()`'s schedule-clearing side effect), sets
full brightness, then loops continuously through LED indices 0–50: lights the current index full
white with all others off, prints the index to the terminal, holds for 10 seconds, advances
(wrapping back to 0 after 50). Ctrl+C clears the display and disconnects cleanly before exit.

### Acceptance criteria

- [x] Given the script starts, when it connects, then it does not call `clear_schedule()` or otherwise touch the charger's active charging schedule
- [x] Given the script is running, when an LED index is lit, then it is full white (`{"r": 1.0, "g": 1.0, "b": 1.0}`) at full brightness, all other 50 indices are off, and the index number is printed to the terminal
- [x] Given an index has been lit for 10 seconds, when the hold expires, then the script advances to the next index, wrapping from 50 back to 0
- [x] Given the operator presses Ctrl+C, when the script catches the interrupt, then it sends `effect_name="none"` to clear the display, disconnects the websocket, and closes the REST client before exiting

---

## Interactive LED map page (#99)

**Blocked by**: None

**User stories**: 4

### What to build

`scripts/led_map.html` — a local, git-committed page showing the ring (39 positions) and bolt (12
positions) diagram with an editable text label next to each of the 51 LED positions. Labels
persist to `localStorage` as the operator types and are restored on reload. An Export button
downloads the full `{index: label}` mapping as JSON.

### Acceptance criteria

- [x] Given the page is opened in a browser, when it loads, then it renders 51 labelled positions across the ring and bolt shapes, each with an editable text input
- [x] Given the operator types into an input, when the value changes, then it is written to `localStorage`
- [x] Given the page is reloaded after labels were entered, when it loads, then the previously entered labels are restored into their inputs
- [x] Given the operator clicks Export, when triggered, then a JSON file downloads containing all 51 index-to-label entries
- [x] Given `localStorage` is unavailable or throws, when the page loads or a label changes, then the page still renders and functions with empty/unsaved labels rather than erroring

---
