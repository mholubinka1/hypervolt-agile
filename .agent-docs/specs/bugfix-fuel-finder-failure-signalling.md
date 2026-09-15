# Deepen FuelFinderClient's failure signalling

## Problem Statement

`FuelFinderClient.average_price_near` collapses four genuinely different failure modes — a
postcode that won't geocode, a failed fetch of the station list, a failed fetch of the price list,
and no nearby station reporting the requested fuel type — into the same `None` return value. Its
one caller, `DynamicChargingThresholdExtension._poll_once`, can't tell these apart, so it always
clears the cached threshold with the same message: "found no fuel price near `{postcode}` for fuel
type `{fuel_type}`." That message is actively misleading for three of the four cases — it implies
"no station sells this fuel" when the real cause might be "couldn't reach the Fuel Finder API at
all" or "the postcode itself doesn't resolve." The true cause is only visible by separately reading
`FuelFinderClient`'s own internal warning logs, which an operator has no reason to correlate with
the extension's own "cleared the cached threshold" line unless they already suspect this exact bug.

## Solution

Give `average_price_near` a typed return value that distinguishes its four failure modes instead of
collapsing them to `None`, and have the caller produce an accurate, failure-specific message instead
of today's one-size-fits-all wording. `FuelFinderClient`'s internal implementation (geocoding,
pagination, auth-retry, distance ranking) is unchanged — only the outermost return value and how
the caller reacts to it change.

## User Stories

1. As an operator whose dynamic charging threshold extension stops producing a value, I want the log
   to state the actual reason (API unreachable vs. no matching station vs. bad postcode), so that I
   can tell a transient API problem apart from a genuine "no fuel sold near me" configuration issue
   without cross-referencing a second log line.
2. As a developer maintaining `FuelFinderClient`, I want its failure taxonomy to live in its return
   type rather than only in scattered log messages, so that a caller (or a test) can branch on a
   value instead of grepping log text.

## Implementation Decisions

- **New `FuelPriceFailure` enum** in `app/fuel_finder/client.py`, with four members:
  - `GEOCODE_FAILED` — the postcode didn't resolve (404 from postcodes.io, or an HTTP error calling
    it).
  - `STATIONS_UNAVAILABLE` — fetching the station list failed (any HTTP/auth failure reaching
    `/api/v1/pfs`).
  - `PRICES_UNAVAILABLE` — fetching the price list failed (any HTTP/auth failure reaching
    `/api/v1/pfs/fuel-prices`).
  - `NO_MATCHING_STATION` — both fetches succeeded, but no station within the given radius reports
    the requested fuel type.
- **`average_price_near`'s return type changes from `float | None` to `float | FuelPriceFailure`.**
  Each of its four existing early-return points (`_geocode` returning `None`, `_fetch_all_stations`
  returning `None`, `_fetch_all_prices` returning `None`, and the final empty-`_matching_prices`
  check) returns the matching enum member instead of `None`. No other logic changes — the private
  helper methods (`_geocode`, `_fetch_all_stations`, `_fetch_all_prices`, `_paginate`,
  `_authenticated_get`, `_retry_after_unauthorized`, `_price_for`, `_haversine_miles`) keep their
  current `T | None` signatures; only the public method's own boundary translates `None` into a
  specific reason. `FuelFinderClient`'s own internal warning logs (geocode failure, HTTP failure,
  no-match) are unchanged — this doesn't touch what `FuelFinderClient` itself logs, only what it
  returns.
- **Caller (`extensions/behaviours/dynamic_charging_threshold.py`) branches on the failure reason.**
  `_poll_once` currently does `if _price is None: self._clear_cache(<one generic message>)`. It now
  checks `isinstance(_result, FuelPriceFailure)` and passes a distinct, accurate message per member
  to `_clear_cache` (e.g. "could not resolve postcode `{postcode}`" for `GEOCODE_FAILED`, "could not
  fetch the station list" for `STATIONS_UNAVAILABLE`, "could not fetch fuel prices" for
  `PRICES_UNAVAILABLE`, and today's existing "found no fuel price near `{postcode}` for fuel type
  `{fuel_type}`" wording — now correctly scoped to only the `NO_MATCHING_STATION` case it actually
  describes). Everything downstream of that branch (clearing `self._threshold`, the exception
  safety net, the invalid-value/invalid-threshold checks once a real price is returned) is unchanged.

## Testing Decisions

- `tests/fuel_finder/test_client.py`: every existing test that currently asserts
  `_average is None` changes to assert the specific `FuelPriceFailure` member instead — the failure
  mode each test's mocked scenario represents is already unambiguous from its own setup (e.g. the
  404-geocode test asserts `GEOCODE_FAILED`; the "no station reports the fuel type" test asserts
  `NO_MATCHING_STATION`). The existing 401/5xx/network-exception tests all fail during the
  station-list fetch (it's called before the price-list fetch), so they all assert
  `STATIONS_UNAVAILABLE` — add one new test with stations succeeding and only the price-list fetch
  failing, asserting `PRICES_UNAVAILABLE`, since no existing test distinguishes it from
  `STATIONS_UNAVAILABLE` today.
- `tests/extensions/behaviours/test_dynamic_charging_threshold.py`: the existing
  `test_a_poll_finding_no_matching_fuel_type_clears_a_previously_cached_threshold` test keeps
  asserting `get_threshold()` is `None` afterward (caller-facing behaviour is unchanged), but gains
  an assertion that the warning log now states the fuel-type-specific wording. Add new tests proving
  the other three failure reasons each produce their own distinct `_clear_cache` message (geocode
  failure, station-fetch failure, price-fetch failure) — exercised through the same real
  HTTP-mocked chain this file already uses throughout, not by mocking `FuelFinderClient` directly.

## Out of Scope

- Any change to `FuelFinderClient`'s own internal warning-log wording (geocode/HTTP failure
  messages) — those already exist and are accurate; this only changes what the method *returns*,
  not what it logs internally.
- Any change to `_poll_once`'s handling of a returned float (the invalid-price / invalid-threshold
  validation, the cached-value comparison, the exception safety net) — those are unaffected by this
  change.
- Architecture-review candidates #5 (`behaviour.py` config mutation) and #6 (`config.py`↔`led.py`
  import cycle) — tracked and worked separately.

## Further Notes

Source: architecture-review report generated 2026-09-15, candidate #3 ("Deepen FuelFinderClient's
failure signalling"). Grounded directly against the current `app/fuel_finder/client.py` and its one
caller rather than re-deriving the design from the report's diagrams alone; confirmed via grep that
`DynamicChargingThresholdExtension` is the only caller of `average_price_near` in the repo.
