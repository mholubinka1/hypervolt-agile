# Support the Octopus Go tariff

## Problem Statement

The owner's Octopus account moved from Agile to Go (`E-1R-GO-VAR-22-10-14-C`) on 2026-11-05. The scheduler refuses any tariff that isn't Agile when it starts, so the production container now crash-loops and no charging schedule reaches the Hypervolt charger.

Go also publishes its prices differently. Agile prices come as half-hour slots. Go prices come as a few long windows: a cheap overnight window (for example 9.4p/kWh from 00:30 to 05:30 UTC) and a standard rate the rest of the day. The first window returned can have started before now. Schedule building assumes every Price is one half-hour, so lifting the tariff check alone would produce wrong schedules.

## Solution

The scheduler starts normally on Go and builds its schedule with the existing logic: the cheapest half-hours under the price limit, up to the configured duration. In practice that means charging inside the cheap overnight window. If it starts or refreshes partway through a window, only the half-hours that haven't ended yet are considered. Agile keeps working unchanged. Any other tariff still stops the app at startup, and the error names the tariff code.

## User Stories

1. As the charger owner on Octopus Go, I want the scheduler to start without crashing, so that my car gets a charging schedule again.
2. As the charger owner on Octopus Go, I want charging scheduled in the cheapest half-hours under my price limit, so that the car charges in the cheap overnight window, just as it chose the cheapest Agile slots before.
3. As the charger owner, I want half-hours that have already ended left out of the schedule when the app starts or refreshes mid-window, so that no charge time is counted against slots that are gone.
4. As the charger owner, I want Agile to keep working, so that switching back to Agile needs no code change.
5. As the charger owner on an unsupported tariff, I want a startup error that names my tariff code, so that I can see straight away why the app won't run.

## Implementation Decisions

- Only the Octopus client (`AgileClient`) changes. `ScheduleBuilder`, `Scheduler` and everything downstream stay as they are. They keep treating one Price as one half-hour charging period.
- Tariff check: a tariff is supported when its product code (taken with the existing product-code regex) starts with `AGILE-` or `GO-`. The `GO-` prefix deliberately excludes Intelligent Go (`INTELLI-`). Any other tariff raises the existing startup error, now naming the tariff code.
- Price normalisation in `get_upcoming_prices`: each rate the standard-unit-rates endpoint returns is split into back-to-back half-hour Price slots with the rate's value, from `valid_from` to `valid_to`. A final partial slot is clipped to `valid_to`. Slots whose `valid_to` is at or before the fetch time are dropped. Agile rates are already half-hours, so they pass through unchanged apart from the ended-slot filter. The current slot is still kept, which matches the existing replug filter. The result is returned soonest first: every Go cheap window has the same price, and the builder picks among equal prices in input order, so this makes it favour the nearer night over the API's newest-first order.
- No API contract, config or schema changes. Same endpoint, same query period.
- No ADR: the decision is local to one module and easy to reverse.

## Testing Decisions

- Test behaviour at the HTTP boundary. Drive `AgileClient` through tariff discovery and `get_upcoming_prices()`, and assert on the returned Price list and on errors, not on internals.
- Seam: the test replaces the client instance's `httpx.AsyncClient` with one on `httpx.MockTransport` that serves account and standard-unit-rates JSON. The user agreed to this so production code doesn't change.
- Times are built relative to the current half-hour, so tests stay deterministic without freezing the clock.
- Scenarios: Go accepted and priced from the Go product; Agile still accepted; an unsupported tariff rejected with its code in the error; Go windows split into half-hour slots, returned soonest first, with the right values, a final partial slot clipped; ended slots dropped while the current slot is kept; Agile half-hours unchanged.
- Prior art: `tests/fuel_finder/test_client.py` uses `httpx.MockTransport` in the same way.

## Out of Scope

- Intelligent Go and any tariff other than Agile and Go.
- Renaming `AgileClient`, "Agile" log messages or variables.
- Renaming the repo and service to `hypervolt-octopus-scheduler` (separate, later work).
- Making `ScheduleBuilder` aware of variable-length periods.

## Further Notes

- Deploying needs only the new image. There are no config or extension-file changes, so pulling and recreating the container on the Pi is enough, without waiting for the 03:00 UTC Watchtower poll.
- Go's cheap window is fixed at 00:30–05:30 in local time. The API returns it in UTC, and the half-hour split works the same either way.
