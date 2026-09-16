# Break the config.py ↔ hypervolt/led.py import cycle

## Problem Statement

`config.py` imports `parse_window_date`, `window_for_year`, `REFERENCE_ANCHOR_YEAR`, and
`DEFAULT_BUILT_IN_THEMES` from `hypervolt/led.py` (for its date-window validators and to build the
set of reserved built-in theme names), while `hypervolt/led.py` imports `BuiltInLedTheme`,
`CustomLedTheme`, `ExtensionEntry`, and `LedConfig` back from `config.py` (for its own
theme/extension-loading function signatures). This is a genuine cycle, held apart only by
`from __future__ import annotations` and a `TYPE_CHECKING` guard on the `led.py` side — not a real
seam, just an artifact of two files needing pieces of each other. A future provider kind needing the
same date-window shape (the still-unbuilt Volvo `VehicleProvider`, issue #124/#131) would deepen the
cycle further rather than have anywhere clean to depend on it from.

The date-window parsing (`parse_window_date`, `window_for_year`, `REFERENCE_ANCHOR_YEAR`) and the
raw built-in-theme catalog (window + name, independent of `LedTheme`) aren't inherently LED-specific
— they're generic calendar-window concepts that happen to live in `hypervolt/led.py` today only
because LED was the first (and so far only) consumer.

## Solution

Extract the date-window parsing and the raw built-in-theme catalog data into a new shared module,
`common/calendar_window.py`, that both `config.py` and `hypervolt/led.py` depend on
one-directionally. `hypervolt/led.py` keeps its `DEFAULT_BUILT_IN_THEMES` (the `LedTheme`-wrapped
catalog other code already imports) but builds it by wrapping the new module's raw catalog data,
rather than hand-writing `LedTheme` instances directly. `config.py` reads the raw catalog and the
parsing functions from the new module instead of from `hypervolt/led.py`. After this change,
`config.py` imports nothing from `hypervolt/led.py` at all — the only remaining dependency between
the two files is `hypervolt/led.py`'s existing, legitimate, one-directional need for `config.py`'s
Pydantic types (`BuiltInLedTheme`, `CustomLedTheme`, `ExtensionEntry`, `LedConfig`) in its own
extension/theme-loading function signatures, which is not part of the cycle and is left untouched.

## User Stories

1. As a developer adding a new provider kind that needs date-window parsing (the planned Volvo
   `VehicleProvider`), I want a shared, LED-agnostic home for that logic, so that I depend on a
   genuine utility module instead of importing from `hypervolt/led.py` and deepening an existing
   cycle.
2. As a developer reading `config.py` or `hypervolt/led.py`, I want their import relationship to
   reflect what each file actually needs from the other, so that `TYPE_CHECKING` guards signal a
   real design choice rather than papering over a circular import.

## Implementation Decisions

- **New module `app/common/calendar_window.py`**, holding exactly what's generic (nothing
  `LedTheme`-specific):
  - `Window = tuple[int, int, int, int]` (moved verbatim).
  - `REFERENCE_ANCHOR_YEAR = 2000` (moved verbatim).
  - `parse_window_date(value: str) -> Window` (moved verbatim, including its `REFERENCE_ANCHOR_YEAR`
    dependency, now local to this module).
  - `window_for_year(start: Window, end: Window, anchor_year: int) -> tuple[datetime, datetime]`
    (moved verbatim).
  - `DEFAULT_BUILT_IN_THEME_WINDOWS: list[tuple[str, Window, Window]]` — the raw built-in-theme
    catalog as `(effect_name, start, end)` tuples, with no `LedTheme` involved. This is today's
    `DEFAULT_BUILT_IN_THEMES` literal with each `LedTheme(effect_name="...")` replaced by the bare
    name string.

- **`hypervolt/led.py`**: removes its own `Window`, `REFERENCE_ANCHOR_YEAR`, `parse_window_date`,
  `window_for_year` definitions and the hand-written `DEFAULT_BUILT_IN_THEMES` literal. Imports
  `Window`, `parse_window_date`, `window_for_year`, `DEFAULT_BUILT_IN_THEME_WINDOWS` from
  `common.calendar_window` at module level (not `REFERENCE_ANCHOR_YEAR` — nothing in `led.py` uses it
  directly once `parse_window_date` itself moves). `DEFAULT_BUILT_IN_THEMES` becomes a derived list:
  `[(LedTheme(effect_name=name), start, end) for name, start, end in DEFAULT_BUILT_IN_THEME_WINDOWS]`.
  Everything else in `led.py` that already references `Window`, `parse_window_date`, `window_for_year`,
  or `DEFAULT_BUILT_IN_THEMES` (its own internal logic in `load_custom_themes`, `load_built_in_themes`,
  `_resolve_from`, etc.) is unchanged — only where these names come from changes. Because `led.py`
  imports `Window` (and the others) at module level, every existing external importer of `Window`
  from `hypervolt.led` (e.g. `schedule/coordinator.py`) keeps working unchanged — Python makes an
  imported name available as an attribute of the importing module automatically, so this is not a
  re-export shim, just an ordinary import. `led.py`'s existing `TYPE_CHECKING` import of
  `BuiltInLedTheme`/`CustomLedTheme`/`ExtensionEntry`/`LedConfig` from `config.py` is untouched —
  it's a separate, legitimate, one-directional dependency (those loader functions' own type hints),
  not part of the cycle this candidate removes.

- **`config.py`**: replaces `from hypervolt.led import (DEFAULT_BUILT_IN_THEMES,
  REFERENCE_ANCHOR_YEAR, parse_window_date, window_for_year)` with `from common.calendar_window import
  (DEFAULT_BUILT_IN_THEME_WINDOWS, REFERENCE_ANCHOR_YEAR, parse_window_date, window_for_year)`.
  `_RESERVED_LED_EFFECT_NAMES = {theme.effect_name for theme, _, _ in DEFAULT_BUILT_IN_THEMES}`
  becomes `_RESERVED_LED_EFFECT_NAMES = {name for name, _, _ in DEFAULT_BUILT_IN_THEME_WINDOWS}` —
  same set of names, built from the raw catalog instead of unwrapping `LedTheme` objects. `config.py`
  no longer imports anything from `hypervolt.led`.

## Testing Decisions

- New `tests/common/test_calendar_window.py`: moved from `tests/hypervolt/test_led_parse_window_date.py`
  (which tested `parse_window_date` via its old home), importing from `common.calendar_window`
  instead — matches this repo's own convention of one test file per module living alongside what it
  tests, and stops the test suite from silently exercising a function only through
  `hypervolt.led`'s pass-through import once that's no longer the function's real home. The moved
  `parse_window_date` tests carry over unchanged. `window_for_year` moved into the same module but had
  no dedicated test anywhere before this — it was only covered indirectly through `config.py`'s
  validators and `led.py`'s `_resolve_from` — so this file also gains two direct tests for it
  (same-year vs. year-wrap), a reasonable strengthening now that it has a real standalone home worth
  testing directly, not required by the cycle-breaking goal itself.
- `tests/hypervolt/test_led.py` and `tests/hypervolt/test_led_resolve_custom_themes.py`: both import
  `DEFAULT_BUILT_IN_THEMES` from `hypervolt.led` — unaffected, since `led.py` still defines and
  exports it (now derived rather than hand-written, but identical in value and type). No changes
  needed; confirm they still pass as regression proof that the derived list is equivalent to the old
  literal.
- `tests/schedule/test_coordinator.py`: imports `Window` from `hypervolt.led` — unaffected for the
  same reason (module-level import makes it available as an attribute automatically). Confirm it
  still passes unmodified.
- New test proving `config.py`'s validators still work correctly after the import repoint — the
  existing `_WindowedLedTheme` validator tests (wherever they live) already cover
  `parse_window_date`/`window_for_year`'s behaviour through `config.py`; no new scenario is needed,
  just confirmation the existing tests still pass against the repointed import.
- No test needed for `DEFAULT_BUILT_IN_THEME_WINDOWS` itself in isolation beyond what
  `test_calendar_window.py` covers for the parsing functions — the catalog is static data, and its
  correctness as consumed by both `config.py` (name set) and `led.py` (derived `LedTheme` list) is
  already covered by each consumer's own existing tests.

## Out of Scope

- Any change to `led.py`'s `TYPE_CHECKING` import of `config.py`'s Pydantic types — that's a
  separate, legitimate one-directional dependency, not part of the cycle.
- Any change to `Window`'s shape, `parse_window_date`'s parsing rules, or `window_for_year`'s
  year-wrap logic — this is a pure relocation, not a redesign.
- Building anything for the still-unimplemented Volvo `VehicleProvider` (issue #124/#131) — this
  candidate only clears the path for it to depend on `common.calendar_window` cleanly later, it
  doesn't implement that dependency.
- The other architecture-review candidates worked separately: #1/#2/#4 (PR #175), #3 (PR #177), #5
  (PR #179).

## Further Notes

Source: architecture-review report generated 2026-09-15, candidate #6 (flagged "Speculative" —
lowest-confidence tier — since no ADR forbids the current coupling and it isn't urgent on its own,
but worth doing before the Volvo work adds a third consumer of this shape). Grounded directly against
the current `app/hypervolt/led.py` and `app/config.py` rather than the report's diagrams alone: the
report's own "Solution" line ("extract a shared calendar-window module both depend on
one-directionally") only names the date-window parsing, but grounding against the actual code showed
`config.py` also depends on `hypervolt/led.py` for `DEFAULT_BUILT_IN_THEMES` (the built-in theme
catalog) — a second, independent reason for the same import edge. Extracting only the date-parsing
functions would have left that second edge intact and not actually broken the cycle; this spec
extracts the catalog's raw (non-`LedTheme`) data too, which is what makes `config.py`'s dependency on
`hypervolt.led` fully disappear.
