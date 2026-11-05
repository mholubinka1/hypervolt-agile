# Issues: feature-record-fuel-threshold-every-interval

> Work complete — PR ready to merge.

## Record the fuel price and threshold every update interval

**GitHub issue**: #206

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5, 6, 7, 8, 9

### What to build

`Scheduler.update()` replaces the fuel price change check with a timed record step. It checks on every coordinator cycle, and once a "next record due" instant is reached (due at once after start) it reads the fuel price and dynamic threshold through the bounded helper, derives the effective limit through `ThresholdPolicy`, and records a Fuel threshold record. The due instant moves forward one `update_every_mins` after a row is attempted or an accessor times out. It stays put while there is no storable price, so recording retries next cycle. `FuelThresholdRecorder.record()` writes only rows that carry a storable fuel price and reports whether it attempted one. `fuel_price_changed` and the last-price memory go. The rebuild path stops recording. Info-level logging is unchanged.

### Acceptance criteria

- [x] Given a provider with a fuel price and threshold, when `update()` first runs, then one row holds the price, threshold, effective limit and source.
- [x] Given a row was just written, when `update()` runs again within the interval, then no further row is written.
- [x] Given the interval has passed and the price is unchanged, when `update()` runs, then another row is written.
- [x] Given the provider has no fuel price, when `update()` runs, then no row is written, and a row is written on the first later cycle that has one.
- [x] Given an absent or unstorable threshold beside a price, then no row is written.
- [x] Given a static cap below the dynamic threshold, then the row's source is `static cap`.
- [x] Given a schedule rebuild with no due interval, then the rebuild writes no row.
- [x] Given a hung accessor, then it times out with a warning and is not called again within the interval.
- [x] Given a failing database, then the write is attempted once per interval, not once per cycle.
- [x] Given no recorder or no threshold provider, then nothing is recorded.
- [x] Given an unexpected failure in the record step, then `update()` does not raise.
- [x] No info-level log line is added, removed or changed.

---
