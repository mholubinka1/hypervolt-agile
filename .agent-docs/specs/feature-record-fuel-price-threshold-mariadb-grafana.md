# Record fuel price and charging threshold to MariaDB for Grafana

## Problem Statement

The dynamic charging threshold is derived from the local average fuel price, but neither the fuel price nor the resulting threshold is kept anywhere beyond a log line. The electricity Grafana dashboard (backed by the `home_monitoring` MariaDB on the Pi) therefore cannot show what fuel price the scheduler is reacting to, how it has moved, or where the charging limit sits against the Agile prices.

## Solution

Each time the scheduler rebuilds the schedule and resolves a limit, it stores a **Fuel threshold record** — timestamp, fuel price, dynamic charging threshold, effective limit and the limit's source — in a new `fuel_price_threshold` table in `home_monitoring`. The table is created by the app itself. A Grafana artefact in the repo provides the Agile prices panel with the effective limit as a constant dashed horizontal line, a gauge for the latest fuel price, and a timeline of fuel price against the effective limit, ready to merge into the electricity dashboard.

## User Stories

1. As the operator, I want each resolved limit stored with a timestamp, so that I can see how the charging limit has moved over time.
2. As the operator, I want the fuel price stored alongside the threshold, so that I can see what drove each change.
3. As the operator, I want the effective limit and its source (`dynamic`, `cached dynamic`, `static`, `static cap`) stored, so that I can tell when the static ceiling or a stale value was in force.
4. As the operator, I want the table created automatically and idempotently on startup, so that no manual migration is needed.
5. As the operator, I want database connection details in `config.yml`, so that no credentials are hardcoded.
6. As the operator, I want recording to be entirely optional, so that an install without the database behaves exactly as today.
7. As the operator, I want charging to be scheduled normally when MariaDB is down or slow, so that a monitoring problem never costs me a charge.
8. As the operator, I want a warning logged when a row cannot be written, so that I can notice a broken recording pipeline.
9. As the operator, I want my database password never written to logs or error messages, so that secrets stay secret.
10. As the operator, I want the Agile prices panel to show the effective limit as one dashed horizontal line across the whole graph, so that I can see at a glance which half-hours are below the limit.
11. As the operator, I want that line hidden from the legend and tooltip, so that it reads as a threshold marker rather than another data series.
12. As the operator, I want a dial showing the latest fuel price, so that I can see the current driving cost at a glance.
13. As the operator, I want a timeline of fuel price and effective limit, so that I can correlate fuel price moves with the limit.
14. As the operator, I want the Grafana artefact kept in the repo at `data/grafana/fuel-threshold.json`, so that the dashboard work is versioned with the code that feeds it.
15. As the operator, I want extensions written before this feature to keep working, so that a missing fuel-price accessor does not break the threshold flow.

## Implementation Decisions

- **Table `fuel_price_threshold`** in `home_monitoring`: auto-increment big-int primary key; `recorded_at` (naive UTC datetime, matching the other tables, indexed); `fuel_price_pence_per_litre` (decimal, nullable); `dynamic_threshold_incl_vat` (decimal, nullable); `effective_limit_incl_vat` (decimal, not null); `limit_source` (short string, not null). Fuel price and dynamic threshold are NULL when the extension reported nothing this cycle (static, or cached dynamic), and also when a reported value is not a finite positive number that fits its column (fuel price below 10000, threshold below 1,000,000) — the valid limit row is kept rather than lost to a rejected insert. The fuel price is also NULL whenever the stored threshold is NULL, so a row never pairs a price with a missing threshold.
- **Creation** is idempotent via SQLAlchemy metadata `create_all` (checks first), run once when the recorder is built at startup. The existing `energy-monitor` user already has full rights on `home_monitoring`.
- **Library**: SQLAlchemy 2 with PyMySQL, matching the sibling `home-monitoring` repo. Calls are synchronous, so the recorder runs them with `asyncio.to_thread` to avoid blocking the event loop. Connections come from SQLAlchemy's default pool, pre-pinged before reuse (one write per update cycle), with no further pool tuning.
- **Config**: optional top-level `database:` block (host, port, user, password, name) validated by pydantic with `extra="forbid"`, mirroring the existing config models. Absent block means no recorder is created. The password is held as a secret and excluded from reprs, logs and validation error output.
- **New module `FuelThresholdRecorder`** owns the table definition, creation and the insert. Its single public write method accepts the values to record and never raises: any database error is caught and logged as a warning, and the row is dropped (no retry queue). Each database call is bounded by a timeout: driver connect/read/write timeouts plus an overall `asyncio.wait_for` around the worker thread.
- **Extension interface**: `BehaviourProvider` gains an *optional* `get_fuel_price()` returning the fuel price in pence per litre, or `None` when unavailable. It is discovered like the optional lifecycle hooks (ADR 0005): not a required Protocol member, so existing extensions keep working. The shipped dynamic charging threshold extension implements it by returning the price from its last successful poll, cleared on the same failure paths that clear the threshold.
- **Scheduler wiring**: `Scheduler._rebuild`, after `ThresholdPolicy` resolves a limit, asks the threshold provider for the fuel price (when the provider has the accessor, and only when it returned a fresh dynamic threshold this cycle, so a row never carries a price from a provider whose threshold was absent) and passes fuel price, dynamic threshold, effective limit and source to the recorder. A cold-start skip (no limit resolved) records nothing. The write happens after the schedule is built, and its failure cannot affect the rebuild. The `get_fuel_price()` call is itself bounded by a 5s `asyncio.wait_for`: a hung accessor logs a warning and the row is recorded with a NULL fuel price, so telemetry can never hold up the charging schedule.
- **Grafana artefact** `data/grafana/fuel-threshold.json`: an importable dashboard fragment using the same `DS_MYSQL` datasource input and `home_monitoring` dataset as the existing electricity dashboard, containing (1) a copy of the "Agile Prices: Today/Tomorrow (Actual + Forecast)" panel with a second query (refId B) returning the latest effective limit at `$__timeFrom()` and `$__timeTo()` and an override styling that frame as a dashed, fill-less line hidden from legend and tooltip; (2) a gauge of the latest fuel price (p/litre); (3) a time series of fuel price and effective limit. Merging into `electricity-dashboard.json` in the `home-monitoring` repo is a manual step by the operator.
- **Glossary**: *Fuel threshold record* added to `.agent-docs/context.md`. No new ADR: the seam is cheap to reverse and ADRs 0021/0022 already cover the extension kind and the limit policy.

## Testing Decisions

- Tests assert external behaviour: rows stored, schedule still built, warnings logged, JSON structure. They do not assert internal call sequences.
- **Scheduler seam** (`tests/schedule/test_scheduler_fuel_threshold_recording.py`, a focused sibling of `test_scheduler.py` using the same stub agile client and threshold-provider style): the real recorder against an in-memory SQLite engine. Cover: dynamic limit recorded with price and threshold; static cap recorded with source `static cap`; cached dynamic recorded with NULL price/threshold and source `cached dynamic`; static-only config (no provider) records nothing for fuel price; cold-start skip records nothing; a recorder whose database raises still lets the schedule build; no `database:` config records nothing.
- **Extension seam** (`tests/extensions/behaviours`, alongside existing threshold extension tests): `get_fuel_price()` returns the polled price, and `None` after any failure path that clears the threshold; an extension without `get_fuel_price` is tolerated by the scheduler.
- **Config seam** (`tests/test_config.py`): the `database:` block validates, rejects unknown keys and missing fields, and does not echo the password in error output.
- **Grafana artefact**: one test loads the JSON and asserts valid structure — three panels, the refId B dashed/hidden override, and that every column referenced in its SQL exists in the table definition.
- SQLite substitutes for MariaDB only at the driver boundary. Real MariaDB behaviour (DDL, `$__timeFrom()` queries, rendering in Grafana 10.4.2) is verified manually on the Pi.
- Deterministic: no real clock (inject the timestamp source), no network.

## Out of Scope

- Backfilling historical fuel prices or thresholds.
- Retention, pruning or downsampling of `fuel_price_threshold`.
- Alerting on fuel price or recording failures.
- A retry queue or buffering of rows dropped during a database outage.
- Editing the `home-monitoring` repo's `electricity-dashboard.json` or the live Grafana instance.
- Recording anything other than the fields listed (no per-station prices, no MPG or config snapshot).

## Further Notes

- The dotted line is a flat query-driven series styled as a threshold marker because Grafana 10.4 thresholds are static JSON values and cannot be driven by a query or variable. The existing vertical lines on the dashboard are annotations, which have no horizontal equivalent.
- Deploy requires `config.yml` on the Pi to gain the `database:` block (credentials sourced file-to-file, never typed into a command) and the extension `.py` to be copied to the bind mount, per the existing deployment topology.
- `home_monitoring` tables are otherwise owned by the sibling `home-monitoring` apps; this is the first table owned by `hypervolt-agile`.
