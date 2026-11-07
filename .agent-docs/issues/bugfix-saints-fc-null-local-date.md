# Issues: bugfix/saints-fc-null-local-date

## saints_fc: record fixtures whose dateEventLocal is null

**Blocked by**: None

**User stories**: 1, 2, 3, 4

### What to build

Make the extension work out a fixture's local match date from its kick-off timestamp (converted to
Europe/London), falling back to `dateEventLocal` and then `dateEvent` when there is no parseable
timestamp. A fixture TheSportsDB returns with null local fields is then recorded and lights the strip in
the normal match window. Fixtures that do carry `dateEventLocal` behave as before.

### Acceptance criteria

- [ ] Given the 2026-11-07 payload shape (null `dateEventLocal`/`strTimeLocal`, `strTimestamp` of the form `2026-11-07T15:00:00`), when the extension polls, then the date is recorded and `resolve()` lights the strip inside the match window.
- [ ] Given a UTC timestamp whose Europe/London date differs from its UTC date, when the extension polls, then the fixture is recorded under the London date.
- [ ] Given no timestamp and no `dateEventLocal` but a `dateEvent`, when the extension polls, then the date is recorded with an empty kick-off list (fallback-only).
- [ ] All existing `dateEventLocal`-based tests pass unchanged.

---
