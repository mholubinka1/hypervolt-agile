# Issues: chore-break-config-led-import-cycle

> Work complete — PR ready to merge.

## Extract calendar_window.py so config.py no longer imports from hypervolt/led.py

**GitHub issue**: #180

**Blocked by**: None

**User stories**: 1, 2

### What to build

New module `app/common/calendar_window.py` holding `Window`, `REFERENCE_ANCHOR_YEAR`,
`parse_window_date`, `window_for_year` (moved verbatim from `hypervolt/led.py`), plus a new
`DEFAULT_BUILT_IN_THEME_WINDOWS: list[tuple[str, Window, Window]]` — the raw built-in-theme catalog
as name/window tuples, with no `LedTheme` involved.

`hypervolt/led.py` imports these from the new module instead of defining them itself, and builds its
existing `DEFAULT_BUILT_IN_THEMES` (still `list[tuple[LedTheme, Window, Window]]`, still exported
from `led.py` for its existing consumers) by wrapping each raw catalog entry in a `LedTheme`. Its
`TYPE_CHECKING` import of `config.py`'s Pydantic types is untouched — that's a separate, legitimate
dependency, not part of the cycle.

`config.py` imports `DEFAULT_BUILT_IN_THEME_WINDOWS`, `REFERENCE_ANCHOR_YEAR`, `parse_window_date`,
`window_for_year` from `common.calendar_window` instead of `hypervolt.led`, and builds
`_RESERVED_LED_EFFECT_NAMES` from the raw name tuples instead of unwrapping `LedTheme` objects. After
this change, `config.py` imports nothing from `hypervolt.led`.

### Acceptance criteria

- [x] `config.py` has no import from `hypervolt.led` (grep confirms zero references).
- [x] `hypervolt/led.py`'s `DEFAULT_BUILT_IN_THEMES` is byte-for-byte equivalent in value to today's
      hand-written literal, now derived from `common.calendar_window.DEFAULT_BUILT_IN_THEME_WINDOWS`.
- [x] `config.py`'s date-window validators (`must_be_a_valid_window_date`,
      `end_must_be_after_start`) behave identically to today — same accepted/rejected inputs, same
      error messages.
- [x] Every existing external importer of `Window`, `parse_window_date`, or `DEFAULT_BUILT_IN_THEMES`
      from `hypervolt.led` (e.g. `schedule/coordinator.py`, `tests/hypervolt/test_led.py`,
      `tests/schedule/test_coordinator.py`) continues to work unmodified.
- [x] `hypervolt/led.py`'s `TYPE_CHECKING` import of `config.py`'s types is unchanged.
- [x] Full test suite passes, including a moved `tests/common/test_calendar_window.py` (from
      `tests/hypervolt/test_led_parse_window_date.py`) testing `parse_window_date` against its new
      home directly.

---
