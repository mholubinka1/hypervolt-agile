# Never record the static fallback threshold

## Problem Statement

The static `price_limit_incl_vat` (e.g. 40p) sometimes appears in the `fuel_price_threshold` table and on the Grafana panel as the effective limit. It is a ceiling or fallback, not the threshold the fuel price produced, so it makes the series misleading.

## Solution

A Fuel threshold record is written only when the effective limit is the extension's own threshold (source `dynamic`). When the static limit caps or replaces it, no row is written for that cycle.

## User Stories

1. As the operator, I want only `dynamic` rows in the table, so that the panel never shows the static limit as if it were the computed threshold.
2. As the operator, I want recording to resume on the first cycle the dynamic threshold is back under the static limit, so that the gap is as short as the cap.
3. As the operator, I want the info log unchanged, so that it stays as readable as today.

## Implementation Decisions

- The check sits in the scheduler's timed record step, after `ThresholdPolicy.effective_limit` (which still refreshes its cached dynamic value). A non-`dynamic` source returns without recording and without moving the due instant, so the next cycle retries. The extension accessors return cached values, so the retry is cheap.
- No schema, config or recorder change. Existing `static cap` rows stay.

## Testing Decisions

- Behaviour tests through `Scheduler.update()` with an in-memory database: a capped threshold writes nothing, and a row follows after the interval once the threshold is under the limit.

## Out of Scope

- Deleting existing `static cap`, `static` or `cached dynamic` rows.
- Changing the Grafana dashboard.
