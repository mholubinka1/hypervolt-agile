# Issues: feature-record-fuel-price-threshold-mariadb-grafana

## Record the effective limit to MariaDB

**GitHub issue**: #191

**Blocked by**: None

**User stories**: 1, 3, 4, 5, 6, 7, 8, 9

### What to build

The tracer bullet. Add an optional top-level `database:` config block (host, port, user, password, name; password never logged) and a `FuelThresholdRecorder` that creates the `fuel_price_threshold` table in `home_monitoring` idempotently at startup and inserts a **Fuel threshold record** from the scheduler whenever a rebuild resolves a limit. This slice records the timestamp, effective limit incl VAT and its source; the fuel price and dynamic threshold columns exist but are NULL until the next slice. Database access uses SQLAlchemy 2 + PyMySQL (as in the sibling `home-monitoring` repo) via `asyncio.to_thread`. The recorder never raises: a database failure logs a warning and drops the row, and scheduling carries on.

### Acceptance criteria

- [ ] Given a `database:` config and a resolved limit, when the scheduler rebuilds, then one row holds the effective limit incl VAT, its source and a UTC timestamp.
- [ ] Given no `database:` block, then nothing is recorded and behaviour is identical to today.
- [ ] Given the database raises on insert, then the schedule is still built and a warning is logged that does not contain the password.
- [ ] Given a cold-start skip (no limit resolved), then no row is written.
- [ ] Given the table already exists, then startup neither fails nor alters it.
- [ ] Given each of the four limit sources (`dynamic`, `cached dynamic`, `static`, `static cap`), then the stored `limit_source` matches.
- [ ] Given a `database:` block with an unknown key or a missing field, then config validation fails without echoing the password.

---

## Record the fuel price and dynamic threshold

**GitHub issue**: #192

**Blocked by**: #191

**User stories**: 2, 3, 15

### What to build

Give the `BehaviourProvider` an optional `get_fuel_price()` (pence per litre, or `None`), discovered like the optional lifecycle hooks so older extensions keep working. The shipped dynamic charging threshold extension returns the price from its last successful poll and clears it on the same failure paths that clear the threshold. The scheduler asks for it when present and fills `fuel_price_pence_per_litre` and `dynamic_threshold_incl_vat` on the record.

### Acceptance criteria

- [ ] Given a successful poll, then the stored row has the fuel price and the dynamic threshold.
- [ ] Given a failed poll that cleared the cache and a static limit in force, then price and dynamic threshold are NULL and the source is `static`.
- [ ] Given a failed poll with a cached dynamic limit in force, then price and dynamic threshold are NULL and the source is `cached dynamic`.
- [ ] Given an extension with no `get_fuel_price`, then the row is still written with a NULL fuel price and no error.
- [ ] Given the extension's `get_fuel_price()` raises, then the row is still written with a NULL fuel price.

---

## Agile prices panel with a dashed effective-limit line

**GitHub issue**: #193

**Blocked by**: #191

**User stories**: 10, 11, 14

### What to build

Create `data/grafana/fuel-threshold.json`, an importable dashboard fragment using the existing `DS_MYSQL` input and `home_monitoring` dataset. It holds a copy of the "Agile Prices: Today/Tomorrow (Actual + Forecast)" panel with a second query (refId B) returning the latest effective limit at `$__timeFrom()` and `$__timeTo()`, and an override that draws it as a dashed, fill-less line hidden from the legend and tooltip, so it reads as one horizontal threshold line across the whole graph. Include a structure test for the artefact and a short note on merging it into the electricity dashboard.

### Acceptance criteria

- [ ] The artefact is valid JSON with the Agile prices panel copied faithfully from the existing dashboard.
- [ ] The refId B query returns the latest effective limit at both ends of the time range from `fuel_price_threshold`.
- [ ] The refId B override sets a dashed line, zero fill, and hides the series from legend, tooltip and axis labels.
- [ ] Every column referenced in the artefact's SQL exists in the table definition.
- [ ] The merge steps are documented.

---

## Fuel price gauge and fuel price / limit timeline

**GitHub issue**: #194

**Blocked by**: #192

**User stories**: 12, 13, 14

### What to build

Add to `data/grafana/fuel-threshold.json` a gauge showing the latest fuel price (p/litre) and a time series plotting fuel price against the effective limit, using the recorded data.

### Acceptance criteria

- [ ] The gauge reads the most recent non-NULL fuel price and displays p/litre.
- [ ] The timeline plots fuel price and effective limit over the dashboard time range, each on an appropriately labelled axis.
- [ ] The structure test covers both panels and their SQL columns.

---
