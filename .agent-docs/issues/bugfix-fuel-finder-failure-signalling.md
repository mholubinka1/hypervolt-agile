# Issues: bugfix-fuel-finder-failure-signalling

## Give average_price_near a typed failure reason, and have the caller log it accurately

**GitHub issue**: #176

**Blocked by**: None

**User stories**: 1, 2

### What to build

Add a `FuelPriceFailure` enum to `app/fuel_finder/client.py` with four members: `GEOCODE_FAILED`,
`STATIONS_UNAVAILABLE`, `PRICES_UNAVAILABLE`, and `NO_MATCHING_STATION`. Change
`average_price_near`'s return type from `float | None` to `float | FuelPriceFailure`, returning the
matching member at each of its four existing early-return points instead of `None`. No other
`FuelFinderClient` logic changes.

Update `DynamicChargingThresholdExtension._poll_once` (in
`extensions/behaviours/dynamic_charging_threshold.py`) to check whether the result is a
`FuelPriceFailure` and pass a distinct, accurate reason to `_clear_cache` for each of the four
members, replacing today's single generic message that's used (and is misleading) for all four
cases alike.

### Acceptance criteria

- [ ] A postcode that fails to geocode (404 or HTTP error from postcodes.io) returns
      `FuelPriceFailure.GEOCODE_FAILED`, and the extension's cleared-cache log names the postcode as
      the reason.
- [ ] A failed fetch of the station list returns `FuelPriceFailure.STATIONS_UNAVAILABLE`, and the
      extension's cleared-cache log states the station list couldn't be fetched.
- [ ] A failed fetch of the price list (stations fetched successfully) returns
      `FuelPriceFailure.PRICES_UNAVAILABLE`, and the extension's cleared-cache log states the price
      list couldn't be fetched — distinct from the station-list failure above.
- [ ] No station within the given radius reporting the requested fuel type returns
      `FuelPriceFailure.NO_MATCHING_STATION`, and the extension's cleared-cache log states the
      postcode and fuel type — the only case where today's existing wording is actually accurate.
- [ ] A successful lookup still returns a plain `float`, unaffected by the new failure type.
- [ ] Every existing `average_price_near` test asserting `is None` now asserts the specific
      `FuelPriceFailure` member its scenario represents.

---
