# The charger LED geometry is a hand-maintained JSON, edited on a drag-editor page

`themes/reference/charger_led_map.json` is the single source of truth for where each of the 51
LEDs physically sits. It is **not** the calibration tool's output and **not** generated from code:
it is a committed data file, corrected over time by dragging LED handles at true scale on
`themes/reference/charger_led_map.html` and re-exporting. The seed values came from the geometry
that was iterated against the real charger during the Saints FC theme work.

Two alternatives were rejected. Re-running `scripts/calibrate_leds.py` to (re)derive the map:
the ring came out fine that way, but the operator could not map the lightning bolt reliably by
eye, so the bolt positions are empirical adjustments that no calibration pass reproduces. A
Python generator that computes the JSON from a formula: the layout is not derivable — the bolt
weaves and the ring is asymmetric because that is how the hardware is, not because of any rule.
Keeping the geometry as plain data with a visual editor means a future correction is a drag and a
commit, reviewed as a normal diff, with `tests/themes/test_charger_led_map.py` guarding the
schema. The cost is that the numbers are only as good as the last person's eyeballing; the
editor exists precisely so that improves rather than ossifies.

The 243 × 328 mm body size is the Hypervolt Home 3 technical sheet figure. It is a fixed physical
constant, not a tuning knob, and is deliberately restated where each context needs it — the
`_BODY_W_MM` / `_BODY_H_MM` test constants, the page's SVG `viewBox` and body `<rect>`, and the
prose — rather than threaded through a shared source across Python, JSON and HTML.
