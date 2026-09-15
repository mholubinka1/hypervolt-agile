# Issues: feature-dynamic-charging-threshold

## 1. Generalise the extension loader to support multiple provider protocols

**GitHub issue**: #124 (existing — reused, not created by this feature)

**Blocked by**: None

**User stories**: 6

### What to build

This work already exists as an open GitHub issue from the still-unimplemented Volvo spec
(`.agent-docs/specs/feature-volvo-battery-level.md` slice 1, ADR 0017), with scope identical to
what this feature needs: extract `app/hypervolt/led.py`'s protocol-agnostic loading machinery
(`_load_provider_class`, `load_extensions`, the `sys.modules` workaround, the `extensions_dir`
path-traversal guard, and `ExtensionWrapper`'s isolation/dedup-logging body) into a shared home
parameterised by marker method name. No new issue is created for this slice — whichever branch
lands first does the work; this feature's remaining slices depend on it either way.

### Acceptance criteria

See #124 directly — unchanged by this feature.

---

## 2. BehaviourProvider protocol, config schema, and Scheduler threshold wiring

**GitHub issue**: #157

**Blocked by**: #124

**User stories**: 3, 5, 6

### What to build

Define the `BehaviourProvider` protocol (`__init__(config)`, `async get_threshold() -> float |
None`, optional `start()`/`stop()` lifecycle hooks) loaded through the generalised loader from
issue #124, using `get_threshold` as its marker method (ADR 0021). Add
`AppConfig.threshold_extension: ExtensionEntry | None = None` — a single optional entry, not a
list. `price_limit_incl_vat` stays required regardless of whether `threshold_extension` is set.
Wire `main.py` to load it via the existing `--extensions-dir` flag, the same way LED extensions
load today.

Give `Scheduler` an optional threshold-provider dependency. On each schedule rebuild (tied to the
existing `update_every_mins`/replug cadence, not a new interval), consult the provider's cached
`get_threshold()`: a fresh pence-incl-VAT value converts to exc-VAT (`÷ ELECTRICITY_VAT_RATE`,
matching how the static value already converts) and is used for that rebuild; `None` or no
provider configured falls back to the static `price_limit_incl_vat`, unchanged from today.
`ScheduleBuilder`'s `limit_exc_vat` needs to become updatable per rebuild rather than fixed at
construction.

No real fuel-price integration exists yet at this point — prove the wiring with a toy in-test
`BehaviourProvider` that returns a fixed value.

### Acceptance criteria

- [ ] `BehaviourProvider` protocol is defined and loads through the shared loader using
      `get_threshold` as its marker method
- [ ] `AppConfig.threshold_extension: ExtensionEntry | None` is added; `price_limit_incl_vat`
      remains required in all cases
- [ ] No provider configured → static `price_limit_incl_vat` is used, identical to today's
      behaviour
- [ ] Provider configured and returns a fresh value → that value (converted exc-VAT) is used for
      the rebuild
- [ ] Provider configured but returns `None` → falls back to the static `price_limit_incl_vat` for
      that rebuild
- [ ] Proven end-to-end with a toy in-test `BehaviourProvider`, independent of issues #3/#4

---

## 3. Fuel Finder API client (OAuth2 + station lookup/pricing)

**GitHub issue**: #158

**Blocked by**: None

**User stories**: 2, 7

### What to build

`app/fuel_finder/auth.py`: OAuth2 client-credentials token fetch, kept in memory and refreshed on
expiry — no disk persistence needed (unlike Volvo's user-consent flow, there's no per-user token
to survive a restart).

`app/fuel_finder/client.py`: looks up stations near a configured postcode, filters to those
reporting the configured fuel type, and averages the nearest `station_count` (or those within
`radius_miles`) into a single price. No stations found reports "unavailable" (e.g. returns
`None`), never raises. Error handling: 401 triggers exactly one token refresh-and-retry before
giving up; 5xx/network errors are caught, logged as a warning, and never propagate as an
exception.

Independent of issues #1/#2/#4 — pure HTTP client work, testable in isolation.

### Acceptance criteria

- [ ] `app/fuel_finder/auth.py` fetches and refreshes an OAuth2 client-credentials token, kept
      in memory only
- [ ] `app/fuel_finder/client.py` returns the average price for the configured fuel type across
      the nearest matching stations for a given postcode
- [ ] No matching stations found → reported as unavailable, not an exception
- [ ] A 401 triggers exactly one refresh-and-retry; a second failure is caught and reported
      unavailable, not raised
- [ ] 5xx/network errors are caught, logged as a warning, and never propagate out of the client
- [ ] A failed request never leaks `client_id` or `client_secret` into a logged message (matches
      the existing precedent in `tests/extensions/themes/test_saints_fc.py` guarding against its
      own API key leaking via an httpx exception's default URL-embedding message)

---

## 4. `dynamic_charging_threshold` BehaviourProvider extension, end-to-end

**GitHub issue**: #159

**Blocked by**: #157, #158

**User stories**: 1, 2, 4

### What to build

Implement the breakeven calculation as a standalone, directly testable function: cost per mile on
fuel = `(fuel price per litre × 4.54609 L/gallon) ÷ mpg`; cost per mile on electric = `price per
kWh ÷ mi_per_kwh`; breakeven price/kWh solves those equal; final threshold = `breakeven × 0.8`
(fixed 20% margin).

`extensions/behaviours/dynamic_charging_threshold.py` (pre-adopting the provider-kind-named
subfolder convention from GitHub issue #131 — see ADR 0021's amendment): the thin
`BehaviourProvider` adapter composing the fuel_finder client (#3) and the formula above. Config:
`fuel_type` (`petrol`/`diesel`), `mpg` (required), `mi_per_kwh` (default `3.5`), `postcode`,
`station_count`/`radius_miles`, Fuel Finder `client_id`/`client_secret`. Owns a background polling
task (started in `start()`, cancelled in `stop()`) on the `update_every_mins` cadence, caching the
latest computed threshold so `get_threshold()` never awaits live I/O (ADR 0005's pattern, matching
LED and the planned Volvo extension). Any exception anywhere in the poll is caught and logged
inside the extension itself.

This is the slice that makes the feature end-to-end usable: registering
`threshold_extension: {name: behaviours/dynamic_charging_threshold, config: {...}}` in
`config.yml` now produces a live fuel-aware charging threshold in production.

### Acceptance criteria

- [x] Breakeven/margin formula is implemented as a pure, standalone function and tested with
      hand-picked inputs independent of any I/O
- [x] `mi_per_kwh` defaults to `3.5` when omitted from config
- [x] `extensions/behaviours/dynamic_charging_threshold.py` implements `BehaviourProvider` and
      loads via the same `--extensions-dir` mechanism as other extension kinds
- [x] The background poll runs on the `update_every_mins` cadence, not more often
- [x] `get_threshold()` never awaits a live API call — always returns from cache
- [x] Fuel Finder unavailable (any error case from #3) → `get_threshold()` returns `None` this
      cycle, and the scheduler falls back to the static threshold (via #2) rather than stalling
- [x] End-to-end: with valid fuel type/MPG/postcode/credentials configured, the app computes and
      logs a dynamic threshold each rebuild and the schedule respects it

---
