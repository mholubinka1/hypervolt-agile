# Issues: chore-simplify-dayofweek

> Work complete — PR ready to merge.

## Simplify DayOfWeek enum and add its missing test coverage

**GitHub issue**: #122

**Blocked by**: None

**User stories**: 1, 2

### What to build

Replace `DayOfWeek`'s bitmask tuple values (`monday = (1,)` … `sunday = (64,)`) with `auto()`, and remove the unused `all = (127,)` member. Change `weekday_to_dayofweek` from `mapping.get(weekday, DayOfWeek.all)` to direct dict indexing (`mapping[weekday]`), so an out-of-contract weekday index raises `KeyError` instead of being masked by a dead fallback. No wire format, config, or public interface changes — `DayOfWeek` members already serialise by name, never by value.

**Correction (discovered during implementation):** the claim above that no code outside `app/hypervolt/model.py` references the enum was wrong at the time it was written — `app/hypervolt/charger.py`'s `apply_schedule` sorted sessions via `s.day_of_week.value[0]`, which broke (`TypeError`, caught by `mypy`) once `.value` stopped being a `(n,)` tuple. Fixed in the same change: `HypervoltSession` gained a `sort_key()` method (in `model.py`, keyed on declaration order via a precomputed `DayOfWeek -> int` dict, never `.value`), and `charger.py` now calls it instead of reading `.value` directly — so the claim holds true again for the code as it ended up, but wasn't true throughout the change.

Add `tests/hypervolt/test_hypervolt_model.py` (a new file — this module currently has no test coverage) covering `weekday_to_dayofweek`, `HypervoltSession.parse_from_response`, and `HypervoltSession.create_from_charge_session`, calling each directly with no mocking. Named `test_hypervolt_model.py` rather than `test_model.py`: `tests/common/test_model.py` already exists, and this repo's `tests/` tree has no `__init__.py` package markers, so pytest's default import mode raises a module-basename collision for two same-named test files in different directories.

### Acceptance criteria

- [x] `DayOfWeek` members use `auto()`; `all` member is removed
- [x] `weekday_to_dayofweek` uses direct dict indexing (`mapping[weekday]`)
- [x] `weekday_to_dayofweek` returns the correct `DayOfWeek` member for all 7 weekday indices (0–6)
- [x] `parse_from_response` returns the correct `day_of_week` for a valid single-day session
- [x] `parse_from_response` still raises `ValueError` when `days` has zero or more than one entry
- [x] `create_from_charge_session` produces the correct `day_of_week` for a session within a single local day
- [x] `create_from_charge_session` produces the correct `day_of_week` for both halves of a session that splits at local midnight
- [x] No test or production code reads `DayOfWeek.<member>.value`
- [x] `FEATURES.md` Feature 20 marked complete and moved to `FEATURES_ARCHIVE.md` (local-only, not committed) — no-op: these files don't exist anywhere in this repo

---
