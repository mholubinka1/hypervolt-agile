# Issues: chore-simplify-dayofweek

## Simplify DayOfWeek enum and add its missing test coverage

**GitHub issue**: #122

**Blocked by**: None

**User stories**: 1, 2

### What to build

Replace `DayOfWeek`'s bitmask tuple values (`monday = (1,)` … `sunday = (64,)`) with `auto()`, and remove the unused `all = (127,)` member. Change `weekday_to_dayofweek` from `mapping.get(weekday, DayOfWeek.all)` to direct dict indexing (`mapping[weekday]`), so an out-of-contract weekday index raises `KeyError` instead of being masked by a dead fallback. No wire format, config, or public interface changes — `DayOfWeek` members already serialise by name, never by value, and no code outside `app/hypervolt/model.py` references the enum.

Add `tests/hypervolt/test_model.py` (a new file — this module currently has no test coverage) covering `weekday_to_dayofweek`, `HypervoltSession.parse_from_response`, and `HypervoltSession.create_from_charge_session`, calling each directly with no mocking.

### Acceptance criteria

- [ ] `DayOfWeek` members use `auto()`; `all` member is removed
- [ ] `weekday_to_dayofweek` uses direct dict indexing (`mapping[weekday]`)
- [ ] `weekday_to_dayofweek` returns the correct `DayOfWeek` member for all 7 weekday indices (0–6)
- [ ] `parse_from_response` returns the correct `day_of_week` for a valid single-day session
- [ ] `parse_from_response` still raises `ValueError` when `days` has zero or more than one entry
- [ ] `create_from_charge_session` produces the correct `day_of_week` for a session within a single local day
- [ ] `create_from_charge_session` produces the correct `day_of_week` for both halves of a session that splits at local midnight
- [ ] No test or production code reads `DayOfWeek.<member>.value`
- [ ] `FEATURES.md` Feature 20 marked complete and moved to `FEATURES_ARCHIVE.md` (local-only, not committed)

---
