# Simplify DayOfWeek

## Problem Statement

`DayOfWeek` (`app/hypervolt/model.py`) stores its members as bitmask values (`monday = (1,)` through `sunday = (64,)`, plus `all = (127,)`), a layout designed to support combining days via bitwise OR. That capability is never used and is actively excluded elsewhere: `HypervoltSession.parse_from_response` raises if a session carries anything other than exactly one day. Feature 4 (Startup Reset + Multi-Day Guard, shipped) permanently guarantees no multi-day session ever reaches this code path. The bitmask values are dead design intent that misleads anyone reading the enum without the history, and the unused `all` member compounds the confusion.

## Solution

Replace the bitmask values with `auto()`, and remove the unused `all` member. This is a pure internal cleanup with no behavioural change and no wire-format impact — `DayOfWeek` members already serialise to and parse from plain day-name strings (`"monday"`, etc.), never their numeric value.

## User Stories

1. As a developer, I want `DayOfWeek` to use plain sequential values that match how it is actually used, so that the enum does not carry misleading design intent from a bitwise-combination capability that was never implemented and is now permanently excluded.
2. As a developer, I want `weekday_to_dayofweek` to fail loudly on an input it cannot map, so that an impossible state (a weekday index outside 0–6) surfaces as a bug rather than being silently coerced to a meaningless fallback.

## Implementation Decisions

- **`app/hypervolt/model.py`** — `DayOfWeek` enum: replace all seven bitmask tuple values (`monday = (1,)` … `sunday = (64,)`) with `auto()`. Remove the `all = (127,)` member entirely — besides its own definition, its only other use in the codebase is the `weekday_to_dayofweek` fallback below, confirmed by a repo-wide search.
- **`weekday_to_dayofweek`** — currently `mapping.get(weekday, DayOfWeek.all)`. Python's `datetime.weekday()` is contractually 0–6, so the dict (which already has all seven keys) never misses in practice — the fallback is dead code today. Change to direct indexing, `mapping[weekday]`, so a future out-of-contract input raises `KeyError` immediately instead of being masked by a fallback that no longer has a meaningful target once `all` is removed.
- `parse_from_response` already looks members up by name (`DayOfWeek[_days[0].lower()]`), not by value, so it needs no change. **Correction (discovered during implementation):** the original claim here that no other production code references `DayOfWeek` was wrong — `app/hypervolt/charger.py`'s `apply_schedule` sorted sessions via `s.day_of_week.value[0]`, which broke (`TypeError`, and caught by `mypy`) once `.value` stopped being a `(n,)` tuple. Fixed in the same change: `HypervoltSession` gained a `sort_key()` method (in `model.py`, keyed on declaration order, never `.value`), and `charger.py` now calls it instead of reading `.value` directly.
- No config, wire format, schema, or public interface changes. `HypervoltSession.__str__`, `parse_from_response`, and `create_from_charge_session` all continue to work unchanged since none of them ever read `.value`.

## Testing Decisions

- **Seam:** call the pure functions/static methods directly — `weekday_to_dayofweek`, `HypervoltSession.parse_from_response`, `HypervoltSession.create_from_charge_session`. No I/O, no mocking; this is the highest available seam and matches the existing style in `tests/common/test_model.py` (flat pytest functions, plain asserts, no fixtures).
- **New file:** `tests/hypervolt/test_hypervolt_model.py` (does not currently exist — this closes a pre-existing coverage gap in the same module this refactor touches, required under the repo's coverage-diff CI gate). Named `test_hypervolt_model.py`, not `test_model.py`: `tests/common/test_model.py` already exists, and this repo's `tests/` tree has no `__init__.py` package markers, so pytest's default import mode raises a module-basename collision (`import file mismatch`) between two same-named test files in different directories, regardless of their contents.
- Cases to cover:
  - `weekday_to_dayofweek` returns the correct `DayOfWeek` member for all 7 inputs (0–6).
  - `parse_from_response` returns the correct `day_of_week` for a valid single-day session.
  - `parse_from_response` raises `ValueError` when `days` has zero or more than one entry (existing guard — confirms it still works post-refactor).
  - `create_from_charge_session` produces the correct `day_of_week` for a same-local-day session, and for a session that splits at local midnight (start day and end day differ).
- No test asserts on `DayOfWeek` member `.value` — the refactor deliberately makes that value meaningless/arbitrary (`auto()`), so tests must assert on member identity (`DayOfWeek.monday`) and the `.name` string, never on the numeric value.

## Out of Scope

- Any change to multi-day session support — permanently excluded by Feature 4, not reopened here.
- Any change to wire format, `parse_from_response`'s single-day guard, or `HypervoltSession.__str__`.
- Broader test-coverage work on `app/hypervolt/model.py` beyond the functions this refactor directly touches.

## Further Notes

- Dependency: Feature 4 (Startup Reset + Multi-Day Guard) — already shipped and complete. This spec was previously blocked on it in `FEATURES.md`; that block is now void.
- No ADR raised — this change is easily reversible, not surprising to a future reader once the dead-code history is understood, and involves no genuine architectural trade-off.
- No `.agent-docs/context.md` update — `DayOfWeek`/day-of-week is a general concept, not project-specific vocabulary.
