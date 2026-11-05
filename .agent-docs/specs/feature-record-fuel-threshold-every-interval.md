# Record the fuel price and threshold every update interval

## Problem Statement

A **Fuel threshold record** is written only on a schedule rebuild or when the fuel price changes at its stored precision. Several rules govern it: change detection, a memory of the last stored price, same-cycle dedupe with rebuilds, and NULL-price rows from rebuilds. That makes the table irregular and hard to reason about. The operator wants a simple, regular series in Grafana: the fuel price and the threshold written every time they're fetched, whether or not they've changed.

## Solution

Every update interval (`update_every_mins`, the same cadence the threshold extension fetches the fuel price on), the scheduler writes one Fuel threshold record when the extension has a fuel price. The row carries the fuel price, the dynamic charging threshold, the effective limit and its source. Rebuilds and price changes no longer write rows. Info-level logging is unchanged.

## User Stories

1. As the operator, I want a row written every update interval while a fuel price is available, so that the Grafana timeline has a regular series even when the price is flat.
2. As the operator, I want the first row written as soon as a fuel price is available after a start, so that restarts leave no gap longer than the extension's first poll.
3. As the operator, I want each row to carry the fuel price, dynamic threshold, effective limit and source, so that the dashed limit line stays truthful.
4. As the operator, I want no row when the extension has no fuel price, so that the table holds only real observations and an outage shows as a gap.
5. As the operator, I want schedule rebuilds not to write rows, so that the series has a single, predictable cadence.
6. As the operator, I want a hung accessor bounded and backed off for one interval, so that a misbehaving extension cannot slow charging control.
7. As the operator, I want a database failure to drop that interval's row with a warning, so that an outage never slows the polling loop.
8. As the operator, I want the info-level log unchanged, so that the log stays as readable as it is today.
9. As the operator, I want no schema, config or Grafana change, so that deploy is image-only.

## Implementation Decisions

- **Cadence lives in `Scheduler.update()`.** The on-change check becomes a timed record step that runs on every coordinator cycle but does nothing until a "next record due" instant. That instant starts as "now" (due at once). It moves forward one `update_every_mins` after a row is written or attempted, and after an accessor timeout (the existing backoff, reused).
- **No price means retry next cycle.** While the price, or the row's storable price, is absent, the due instant does not move, so the first row lands as soon as the extension has a price. The accessors return cached values, so a per-cycle retry is cheap.
- **Each due cycle**, the scheduler reads `get_fuel_price` and `get_threshold` through the existing 5s bounded helper. It derives the effective limit through `ThresholdPolicy` (unchanged semantics, which also refreshes its cached dynamic value) and calls `record()`.
- **`FuelThresholdRecorder.record()` writes only rows that carry a fuel price.** It keeps the existing sanitising and pairing rule (a price is stored only beside a storable threshold). When the stored price would be NULL it writes nothing and reports that. It returns whether a row was attempted, so the scheduler advances the cadence only then. `fuel_price_changed` and the remembered last price are removed.
- **The rebuild path no longer records.** Its own `get_fuel_price` call goes too.
- **Failure boundary is unchanged.** The step keeps its own `try`/`except`, which logs and carries on.
- **Logging**: no info-level line is added, removed or changed. The existing warnings (timeout, database failure) and the exception log are kept.
- **Unchanged**: table schema, the `database:` config, the extension and its `get_fuel_price()` contract, and the Grafana dashboard artefact.
- **Consequence accepted**: a recorder without a threshold extension records nothing, and Fuel Finder outages no longer produce NULL-price static or cached-dynamic rows.
- **Row volume**: at most about 48 rows a day at the default 30-minute interval.
- **Glossary**: *Fuel threshold record* is updated. No ADR, because the decision is cheap to reverse.

## Testing Decisions

- Tests assert external behaviour: rows stored, how many, with what values, and whether the loop waited.
- **Scheduler seam** (`tests/schedule/test_scheduler_fuel_threshold_recording.py`, reusing its in-memory SQLite engine, stub agile client and provider helpers; time advanced by patching `schedule.datetime` as existing tests do). Cover:
  - the first row on the first `update()` with a price
  - no further row within the interval
  - a row after the interval with an unchanged price
  - no row without a price, and a row on the next cycle once one appears
  - no row from a rebuild alone
  - fields and sources (`dynamic`, `static cap`)
  - an absent or unstorable threshold beside a price writes no row, because the price can't be stored without it
  - a hung accessor that times out, warns, and is not called again within the interval
  - a failing database attempted once per interval
  - no recording without a recorder or a provider
  - an unexpected failure that does not escape `update()`
- **Recorder** unit tests drop the change-detection cases and add `record()` skipping a row with no storable price.
- Recorder plumbing tests (table creation, timestamps, password hiding, timeouts) keep passing, retargeted from rebuild-driven to interval-driven where they used a rebuild to trigger a write.
- Deterministic: no real clock beyond patched `datetime` or the injected clock, and no network.

## Out of Scope

- Rows when no fuel price is available.
- Any extension change, or a push/callback contract.
- Syncing the record instant to the extension's fetch instant.
- Schema, config, Grafana, retention, backfill or retry-queue changes.

## Further Notes

- The app cadence and the extension's fetch cadence share `update_every_mins` but are not phase-aligned, so a row's price can be up to one interval old.
- Deploy is image-only.
