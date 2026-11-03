# Issues: feature-record-fuel-price-threshold-row

## Record a row when the fuel price changes

**GitHub issue**: #201

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5, 6, 7, 8, 10, 11

### What to build

The tracer bullet. `FuelThresholdRecorder` remembers the last fuel price it stored (after sanitising to the column's three-decimal scale and range) and exposes `fuel_price_changed(price, threshold)`, which is true only when a row for that pair would store a new price (a price beside an unstorable threshold is never a change). `record()` updates that memory whenever the row it writes carries a price, at the moment the write is attempted, and the price/threshold sanitising (including the rule that a price is stored only beside a storable threshold) moves into `record()` so the memory always matches what is stored. `Scheduler.update()` gains a check after the rebuild step, run on every coordinator poll cycle including in Ultra mode: when the extension's price has changed (it reads `get_threshold` alongside the price), it derives the effective limit through the same `ThresholdPolicy` a rebuild uses, and records a Fuel threshold record. Nothing is written when the price becomes unavailable.

### Acceptance criteria

- [ ] Given a provider reporting a fuel price and a dynamic threshold, when `update()` first runs after start, then one row holds that price, the threshold, the effective limit and its source.
- [ ] Given the price is unchanged on later cycles, then no further rows are written.
- [ ] Given the price moves by less than 0.001 p/litre, then no row is written.
- [ ] Given the price changes (including when a static cap is in force), then a new row holds the new price, the threshold, and the effective limit and source at that moment.
- [ ] Given the price becomes unavailable, then no row is written and the last stored price is remembered, so the same price returning is not a change.
- [ ] Given a rebuild and a price change in the same cycle, then exactly one row is written.
- [ ] Given the database fails on a write, then the row is dropped and the failure is not retried on every later cycle.
- [ ] Given a price with no dynamic threshold, or a threshold that resolves no effective limit, then nothing is written and the check repeats on the next cycle.
- [ ] Given no recorder or no threshold provider, then the check does nothing.
- [ ] Existing rebuild-recording behaviour and tests are unchanged.

---

## Bound the change check against a hung accessor

**GitHub issue**: #202

**Blocked by**: #201

**User stories**: 9

### What to build

Extract the fuel price accessor call, with its 5s `asyncio.wait_for` bound and timeout warning, into one helper shared by the rebuild path and the new change check. After a timeout the change check is skipped for one update interval, so a hung or slow third-party accessor cannot slow charging control on every poll cycle. The rebuild path keeps its own once-per-rebuild call and its existing degrade-to-no-price behaviour.

### Acceptance criteria

- [ ] Given `get_fuel_price` hangs, then `update()` returns after the 5s bound with one warning naming the extension.
- [ ] Given a timeout has just happened, then the accessor is not called again until the next update interval.
- [ ] Given the accessor recovers after that interval, then recording on change resumes.
- [ ] Given a rebuild's own fuel price fetch times out, then its existing behaviour is unchanged: the row is recorded without a price, with one warning.

---
