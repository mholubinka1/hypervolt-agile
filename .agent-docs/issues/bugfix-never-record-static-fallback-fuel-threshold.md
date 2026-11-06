# Issues: bugfix-never-record-static-fallback-fuel-threshold

## Skip rows whose limit is not the extension's dynamic threshold

**GitHub issue**: local only

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

The scheduler's timed record step returns without recording when the effective limit's source is not `dynamic`. The due instant is not advanced, so recording resumes on the first cycle the dynamic threshold is under the static limit. Info-level logging is unchanged.

### Acceptance criteria

- [x] Given the static limit is below the dynamic threshold, when `update()` runs, then no row is written.
- [x] Given that cap has lifted, when the next interval's `update()` runs, then a `dynamic` row is written.
- [x] No info-level log line is added, removed or changed.
