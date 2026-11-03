# Record a fuel threshold row whenever the fuel price changes

## Problem Statement

The `fuel_price_threshold` table only gets a row when the schedule is rebuilt, and a rebuild only happens on a replug or when Octopus publishes a new Agile price horizon — roughly once a day. The dynamic charging threshold extension re-polls the fuel price every update interval (30 minutes), so most fuel price moves never reach the table, and the Grafana fuel gauge and timeline show a price that can be many hours stale. While the charger is in Ultra mode the app stands down and rebuilds are rarer still.

## Solution

Whenever the fuel price changes, the scheduler writes a **Fuel threshold record** immediately, independent of Agile price rebuilds. A change is detected on every coordinator poll cycle by comparing the extension's cached fuel price with the last price the recorder stored. The first real price seen after each start is also recorded. A rebuild and a change in the same cycle produce one row, and nothing changes in the schema, the config or the Grafana artefact.

## User Stories

1. As the operator, I want a row written each time the fuel price changes, so that the fuel gauge and timeline follow the real price.
2. As the operator, I want the row's timestamp to be within seconds of the extension's poll, so that the timeline does not smear price moves.
3. As the operator, I want the first real price after each app start recorded, so that restarts and deploys leave no gap in the timeline.
4. As the operator, I want a price difference smaller than the stored precision (0.001 p/litre) ignored, so that floating-point noise in the station average does not create rows.
5. As the operator, I want the change row to carry the same fields as a rebuild row (price, dynamic threshold, effective limit and its source), so that the dashed limit line stays truthful.
6. As the operator, I want a rebuild and a price change in the same cycle to make one row, so that the table has no duplicates.
7. As the operator, I want no row written when the price becomes unavailable, so that the table holds only real observations.
8. As the operator, I want a database failure to drop the row with a warning and not be retried every cycle, so that an outage never slows the polling loop.
9. As the operator, I want a hung or slow fuel price accessor to be bounded and backed off, so that a misbehaving third-party extension cannot slow charging control.
10. As the operator, I want recording on change to work while the charger is in Ultra mode, so that the fuel timeline continues when rebuilds are rare.
11. As the operator, I want no new config and no schema change, so that the live deployment needs only a new image.

## Implementation Decisions

- **Detection** lives in `Scheduler.update()`, after the rebuild step, and runs on every call (once per coordinator poll cycle, including in Ultra mode). It is a no-op unless both a recorder and a threshold provider are configured.
- **`FuelThresholdRecorder` owns "what was last stored".** It remembers the last stored fuel price — the value after sanitising to the column's three-decimal scale and range — and exposes `fuel_price_changed(price, threshold)`, true when a row for that price and threshold would store a usable price that differs from that memory. The first usable price after construction therefore counts as a change. An unusable or absent price is never a change, and neither is a price whose threshold is unstorable: `record()` stores NULL for both in that case, so counting it would write a price-less row on every cycle.
- **`record()` updates the memory** whenever the row it writes carries a fuel price, at the moment the write is attempted rather than when it succeeds. A down database therefore costs one dropped row per change and is never retried on every cycle. Sanitising of the price and threshold (and the existing rule that a price is stored only beside a storable threshold) moves from the insert into `record()`, so the memory always reflects what is actually stored.
- **Each cycle**, the scheduler fetches the price and returns if there is none. Otherwise it reads `get_threshold` and asks the recorder whether the pair is a change. On a change it derives the effective limit through the same `ThresholdPolicy` a rebuild uses (fresh dynamic value, same cached-dynamic semantics) and calls `record()` with the price, the dynamic threshold and the limit. If the dynamic threshold is absent while a price is present (only a third-party extension could do this), nothing is written and the check repeats next cycle.
- **Shared bounded fetch.** The accessor calls the new check makes (`get_fuel_price` and `get_threshold`), each with a 5s `asyncio.wait_for` and a timeout warning that names the accessor, go through one helper; the rebuild path uses it for its own `get_fuel_price` call. After a timeout in either place the new check is skipped for one update interval, so a hung accessor cannot slow every poll cycle. The rebuild keeps its own once-per-rebuild calls.
- **Failure boundary.** The check runs ahead of the charger work in the coordinator's cycle, so it has its own `try`/`except` that logs and carries on: nothing in it can cost a charging cycle.
- **Same-cycle dedupe.** Because the rebuild step runs first and `record()` updates the memory, a rebuild that records the current price makes the subsequent change check see no change.
- **Unchanged:** the table schema, the `database:` config, the extension and its optional `get_fuel_price()` contract, and `data/grafana/fuel-threshold.json` (its queries read the latest row, which is now fresher).
- **Row volume** is bounded by the extension's poll cadence: at most about 48 rows a day at the default 30-minute interval.
- **Glossary:** *Fuel threshold record* in `.agent-docs/context.md` is updated. No ADR: the decision is easy to reverse and consistent with ADRs 0021 and 0022.

## Testing Decisions

- Tests assert external behaviour (rows stored, how many, with what values, whether the polling loop waited), not the recorder's internals.
- **Scheduler seam** (the existing `tests/schedule/test_scheduler_fuel_threshold_recording.py`, reusing its in-memory SQLite engine, stub agile client and provider helpers): repeated `update()` calls with a provider whose price changes between calls. Cover: the first price after start is recorded; an unchanged price writes nothing further; a change smaller than 0.001 writes nothing; a real change writes a row with the new price, threshold, effective limit and source (including the `static cap` case); a price that becomes unavailable writes nothing; a rebuild and a change in the same cycle write one row; a hung accessor times out, warns once, and is not called again within the update interval; a database that fails is attempted once per change, not once per cycle; an absent threshold with a present price writes nothing; and the check is skipped without a recorder or provider.
- **Recorder** unit coverage for `fuel_price_changed` (first price, same price, sub-precision change, unusable price) and for the pair rule: a price beside an unstorable or absent threshold is not a change, and is not remembered.
- Existing rebuild-recording tests must keep passing unchanged; they pin the pairing and sanitising rules the refactor moves.
- Deterministic: no real clock beyond the injected one, no network. SQLite stands in for MariaDB at the driver boundary only.

## Out of Scope

- Recording a row when the price becomes unavailable.
- Any change to the extension, its poll cadence, or a push/callback contract from the extension.
- Backfilling history, retention or pruning, and alerting.
- Schema, config or Grafana changes.
- A retry queue for rows dropped during a database outage.

## Further Notes

- Root cause for context: `Scheduler.update()` is called every poll cycle but only rebuilds on a replug or a new Agile price horizon; the row was written inside that rebuild.
- Deploy is image-only: no `config.yml` change and no extension copy. A recreate of the container is enough.
- The compare-at-stored-precision rule matters because the extension averages several station prices, and the sum order can differ by tiny amounts between polls.
