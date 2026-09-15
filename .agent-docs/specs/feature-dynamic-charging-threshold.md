# Dynamic (Fuel-Aware) Charging Price Threshold

## Problem Statement

Today the charging price threshold (`price_limit_incl_vat`) is a single static number the operator
picks once and edits by hand in `config.yml`. For most EVs that's fine — electricity is always
worth it below some fixed price. But an operator with a plug-in hybrid (PHEV) has a real
alternative to charging: burning fuel instead. A static threshold can't reflect that trade-off, and
petrol/diesel prices move independently of Agile electricity prices, so a number picked last month
can now be badly wrong in either direction — charging on electricity that's actually more expensive
per mile than fuel, or missing electricity that's still cheaper than fuel even above the old static
limit.

## Solution

An opt-in extension, relevant only to plug-in hybrids, that replaces the static threshold with one
recomputed from real fuel prices: it looks up the local price of the operator's chosen fuel type,
combines it with the vehicle's approximate MPG and electric efficiency to find the electricity
price at which a mile costs the same either way, then applies a 20% safety margin below that so
charging only happens when electric is meaningfully — not just marginally — cheaper than fuel.

Operators without this extension registered see no change: `price_limit_incl_vat` keeps working
exactly as it does today, and it remains required even when the extension *is* registered, as the
value the scheduler falls back to whenever the extension has no fresh answer.

## User Stories

1. As a PHEV operator, I want the charging threshold to reflect today's real fuel price rather than
   a number I picked once, so that the scheduler only charges when electricity is genuinely the
   cheaper option per mile.
2. As a PHEV operator, I want to tell the extension my fuel type, my car's approximate MPG, and
   (optionally) its electric efficiency, so that the breakeven calculation matches my actual car
   rather than a generic assumption.
3. As an operator without a PHEV, I want this extension to be entirely optional and off by default,
   so that nothing about my existing static-threshold setup changes.
4. As an operator, I want a 20% margin built into the computed threshold rather than the raw
   breakeven price, so that I'm not charging at a price that's only trivially cheaper than fuel once
   real-world driving variance is accounted for.
5. As an operator, I want the scheduler to fall back to my configured static
   `price_limit_incl_vat` whenever the extension can't produce a fresh value — fuel price API down,
   no nearby stations, extension not yet polled, OAuth failure — so that a third-party data problem
   never stops my car from charging.
6. As a developer, I want this shipped as a `BehaviourProvider` extension through the same
   operator-registered, dynamically-loaded, failure-isolated mechanism LED Theme Extensions already
   use, so that a misbehaving fuel-price integration can never take down core scheduling, lock
   control, or LED themes.
7. As an operator, I want fuel prices sourced from stations actually near me (by postcode), not a
   single arbitrary station or a national average, so that the computed threshold reflects prices I
   could realistically pay.

## Implementation Decisions

### Generalising the extension loader (performs ADR 0017; see ADR 0021)

`app/hypervolt/led.py` currently hardcodes its dynamic-loading machinery
(`_load_provider_class`'s `hasattr(cls, "resolve")` check, `load_extensions`, and
`ExtensionWrapper`'s `.resolve()`/`.resolve_fallback()` dispatch) to `LedThemeProvider` alone. ADR
0017 already decided this should be generalised, parameterised by a provider kind's marker method
name; nothing has built that yet since the Volvo `VehicleProvider` spec (which would otherwise have
been the one to do it) is still unimplemented. This feature performs that extraction, since it's
the first non-LED provider kind actually being built:

- Extract the generic parts (module loading via `_load_provider_class`, the `sys.modules`
  registration workaround, the `extensions_dir` path-traversal guard in `load_extensions`, and the
  failure-isolation/dedup-logging body currently inside `ExtensionWrapper._invoke`) into a shared
  home, parameterised by: the marker method name used to identify a valid provider class, and the
  method(s) the wrapper dispatches through for isolation/dedup.
- `hypervolt/led.py` keeps only what's LED-specific: the `LedTheme` dataclass, theme resolution,
  and a thin `LedThemeProvider`-specific wrapper built on the shared machinery.
- No behavioural change to LED extensions or `extensions/saints_fc.py` — existing LED loader tests
  must keep passing unmodified in intent (adjusted only for whatever import path the shared code
  moves to).

### `BehaviourProvider` protocol (ADR 0021)

Named generically — not `ThresholdProvider` — because this is the pattern for any future
operator-pluggable behaviour, not just this one feature; scope for *this* feature is exactly one
marker method:

- `__init__(self, config: dict) -> None`
- `async def get_threshold(self) -> float | None` — the marker method identifying a valid
  `BehaviourProvider` class. Returns the computed threshold in pence/kWh, VAT-inclusive (the same
  units and convention as `price_limit_incl_vat`), or `None` when no fresh value is available this
  cycle.
- Optional `async def start(self) -> None` / `async def stop(self) -> None` lifecycle hooks,
  handled exactly like LED extensions (ADR 0005): `start()` kicks off the extension's own
  background polling task on its own cadence; the scheduler never awaits live I/O inline.

### Config schema

- New top-level `AppConfig.threshold_extension: ExtensionEntry | None = None` — reuses the existing
  `ExtensionEntry` (`name`, `config: dict`) shape LED extensions already use. A single optional
  entry, not a list: exactly one charging threshold is ever active. Loaded from the same
  `extensions/` directory via the existing `--extensions-dir` flag — no new CLI argument.
- `price_limit_incl_vat` stays required on `Schedule` regardless of whether `threshold_extension` is
  set — it is the fallback value, not replaced by this feature.
- The extension's own `config:` dict (fuel type, MPG, electric efficiency, postcode, station
  count/radius, Fuel Finder OAuth credentials) is defined and validated by the extension itself, not
  by core `app/config.py` — consistent with ADR 0006's "every extension operates from its own config
  block in total isolation."
- Reference extension config fields: `fuel_type` (`petrol` | `diesel`), `mpg` (float, required),
  `mi_per_kwh` (float, default `3.5` — see Further Notes for how that default was chosen),
  `postcode`, `station_count` (int, required, default a small number such as `5`) and optionally
  `radius_miles`. `radius_miles` is an upper-bound cap on `station_count`, not an alternative
  selection mode: stations are always ranked nearest-first and averaging stops once
  `station_count` matches are found; when `radius_miles` is also set, any station beyond it is
  excluded even if fewer than `station_count` matches were found within range (clarified during
  #158/#163's review — Copilot read the original "`station_count` and/or `radius_miles`" phrasing
  as implying two mutually-exclusive modes, which was never the intent).
  `client_id`, `client_secret` (Fuel Finder OAuth2 client-credentials — from the operator's own
  GOV.UK Fuel Finder developer registration, not a shared app credential, matching the Volvo spec's
  precedent of per-operator API credentials).

### Breakeven calculation

- Cost per mile on fuel = `(fuel price per litre × 4.54609 L/gallon) ÷ mpg`.
- Cost per mile on electric = `price per kWh ÷ mi_per_kwh`.
- Breakeven price/kWh is where those are equal: `breakeven = (fuel price per litre × 4.54609 ÷ mpg)
  × mi_per_kwh`.
- Final threshold = `breakeven × 0.8` (a fixed 20% margin — not configurable in this spec).
- This is pure arithmetic with no I/O — implement as a standalone, directly testable function, not
  buried inside the extension's polling/caching code.

### Fuel price source: GOV.UK Fuel Finder API

Confirmed live against the operator's own registered credentials during implementation (not just
docs) — base URL `https://www.fuel-finder.service.gov.uk`, free to use.

- **Auth is not RFC 6749 client-credentials despite the name** — it's a bespoke JSON token
  endpoint: `POST /api/v1/oauth/generate_access_token` with JSON body `{"client_id",
  "client_secret"}` returns `{"success", "data": {"access_token", "token_type": "Bearer",
  "expires_in": 3600, "refresh_token", "refresh_token_expires_in": 172800}, "message"}`.
  `POST /api/v1/oauth/regenerate_access_token` with `{"client_id", "refresh_token"}` gets a new
  access token without re-sending the secret (sample response has no new `refresh_token` — treat
  the refresh token as not rotating; re-run `generate_access_token` once *it* expires at 48h). The
  API's own guidance: reuse a valid token until near expiry, don't fetch a fresh one per call.
  Requests authenticate via `Authorization: Bearer <access_token>`; a missing/expired/invalid token
  gets a 401.
- **No location query parameter exists at all.** `GET /api/v1/pfs?batch-number=N` (station
  info) and `GET /api/v1/pfs/fuel-prices?batch-number=N` (prices) each return up to 500 records
  per batch of the *entire UK national dataset* — there is no postcode/radius filter server-side.
  "Nearest N stations" has to be computed client-side: page through `/pfs` to get every station's
  `node_id` + `location.{postcode, latitude, longitude}`, geocode the operator's configured
  postcode (via `postcodes.io`, free/unauthenticated, confirmed working — `GET
  api.postcodes.io/postcodes/{postcode}` → `result.{latitude, longitude}`), rank stations by
  distance, then page through `/pfs/fuel-prices` to fetch prices and join by `node_id`. An
  incremental variant (`GET /api/v1/pfs/fuel-prices?batch-number=N&effective-start-timestamp=...`)
  fetches only prices changed since a timestamp — cheap re-polling once a baseline is cached,
  since re-fetching the full national dataset every `update_every_mins` cycle would be wasteful
  even though the 100 req/min rate limit technically allows it.
- **Station records carry a `fuel_types` list, and price records carry a `fuel_type` code per
  entry** — observed values `E5`, `E10` (petrol grades), `B7_STANDARD`, `B7_PREMIUM` (diesel
  grades), each with its own `price` (pence/litre) and `price_last_updated` timestamp. The
  extension's `fuel_type: petrol | diesel` config maps to the *standard* grade of each —
  `petrol → E10`, `diesel → B7_STANDARD` — not the premium variants, matching what most UK pumps
  mean by "petrol"/"diesel" without qualification.
- Client-credentials tokens are short-lived and re-requested/refreshed by the client itself, kept in
  memory only — unlike the Volvo spec's user-consent OAuth flow, there is no per-user token to
  persist to disk, so no `token_store_path` equivalent is needed here.

### Package layout

Mirrors the Volvo spec's precedent of a thin `extensions/` adapter backed by a proper `app/`
package for substantial integration logic:

- `app/fuel_finder/auth.py` — OAuth2 client-credentials token fetch and in-memory refresh.
- `app/fuel_finder/client.py` — station lookup by postcode/radius, per-station fuel prices by type.
- `extensions/behaviours/dynamic_charging_threshold.py` — the `BehaviourProvider` adapter: owns a
  background polling task (cadence below), calls `app/fuel_finder` plus the breakeven calculation,
  caches the result, returns it from `get_threshold()`. See ADR 0021's amendment: this pre-adopts
  the provider-kind-named subfolder convention GitHub issue #131 (unimplemented) plans for
  `extensions/`, rather than sitting flat.

### Scheduler wiring

- `Scheduler` (`app/schedule/__init__.py`) gains an optional threshold-provider dependency (the
  loaded `BehaviourProvider`'s wrapper, or `None` when `threshold_extension` isn't configured).
- `ScheduleBuilder` currently fixes `limit_exc_vat` at construction (`app/schedule/builder.py`). It
  needs to become updatable — e.g. a setter, or the value passed into `build()` instead of the
  constructor — so `Scheduler` can refresh it each rebuild rather than only at startup.
- On each schedule rebuild (`_rebuild_on_replug` / `_rebuild_on_new_prices` — i.e. tied to the
  existing `update_every_mins` cadence and replug events, not a new independent interval): if a
  threshold provider is configured, `Scheduler` awaits its cached `get_threshold()`, converts
  pence-incl-VAT → exc-VAT the same way the static config value already is
  (`÷ ELECTRICITY_VAT_RATE`), and uses that as the builder's limit for this rebuild. `None` (no
  fresh value) or no provider configured → use the static `price_limit_incl_vat` value, exactly as
  today.
- The extension's own background poll cadence (inside its `start()` task) reuses
  `config.schedule.frequency` (`update_every_mins`) rather than introducing a second configurable
  interval — fuel prices are far less volatile than Agile electricity prices, and this avoids a
  redundant config field.

### Error handling

All Fuel Finder failures are caught and logged inside the extension itself, never propagating out
of `get_threshold()` — consistent with how the generalised wrapper isolates and dedup-logs every
other provider kind's failures:

- Network / 5xx — log a warning, skip this poll cycle, resume on the next.
- 4xx (bad OAuth credentials, malformed request) — log naming the likely cause (credential
  misconfiguration) so it's actionable.
- No stations found for the configured postcode/fuel type — log a warning; `get_threshold()`
  returns `None`, scheduler falls back to the static threshold.
- 401 — attempt `regenerate_access_token` once using the cached refresh token; if that also fails
  (refresh token itself expired/invalid), fall back to `generate_access_token` with the client
  id/secret; if that also fails, log and report unavailable for this cycle.
- `postcodes.io` geocoding failure (bad postcode, network) — log a warning; `get_threshold()`
  returns `None` for this cycle rather than caching a bad/missing coordinate.

## Testing Decisions

- **Breakeven/threshold formula**: pure function, no I/O — test directly with hand-picked
  fuel-price/MPG/mi-per-kWh inputs, including the 20% margin arithmetic. Highest, cheapest seam in
  this feature.
- **Generalised extension loader**: extend the existing LED loader tests
  (`tests/hypervolt/test_led_load_extensions.py`) to confirm LED behaviour is unchanged post-
  extraction, plus new equivalent tests loading a fake `BehaviourProvider` module to prove the same
  shared loader correctly identifies and wraps a different marker method (`get_threshold`) —
  mirrors the Volvo spec's own testing decision for the identical refactor.
- **Scheduler threshold selection**: test with a real, lightweight in-test `BehaviourProvider`
  implementation (its internal state seeded directly, no mocking), the same pattern
  `tests/schedule/test_coordinator.py` already uses for LED extensions. Cases: provider returns a
  fresh value → builder uses it; provider returns `None` → falls back to static config; no provider
  configured → static config used, unchanged from today's behaviour.
- **`app/fuel_finder/auth.py` and `client.py`**: `httpx.MockTransport` against a real
  `httpx.AsyncClient` — the same convention `tests/extensions/test_saints_fc.py` already uses for
  its own httpx-based extension. Cases: station averaging over N nearby stations, no stations
  found, 4xx/5xx handling, token fetch/refresh. Also required, matching
  `test_saints_fc.py`'s existing precedent in this exact area: a case proving a failed request never
  leaks `client_id` or `client_secret` into a logged exception message (httpx's own exceptions embed
  the full request URL).
- **`extensions/dynamic_charging_threshold.py`**: the thin adapter's own background task
  lifecycle/caching follows the same test shape already proven for `saints_fc.py`'s `start()`/
  `stop()` task management.

## Out of Scope

- Any manufacturer- or model-specific MPG/efficiency lookup — the operator supplies both MPG and
  electric efficiency by hand in config; no VIN decoding or spec-sheet integration.
- Any UI for viewing the computed threshold or fuel price history — this is a logged value, same as
  every other piece of scheduler state in this app.
- Fuel types beyond petrol/diesel, or blending multiple fuel types.
- A configurable safety margin — the 20% deduction is fixed for this spec; making it configurable
  can be a follow-up if it proves wrong in practice.
- A configurable extension poll interval — it reuses `update_every_mins`; a separate interval can be
  a follow-up if fuel-price volatility ever demands finer granularity.
- Any change to how the static `price_limit_incl_vat` behaves for operators who don't register this
  extension.

## Further Notes

- The 3.5 mi/kWh default was chosen from real-world PHEV electric-only efficiency figures gathered
  during this planning session (roughly 3.2–3.8 mi/kWh, e.g. Toyota Prius PHEV), not from this
  codebase — worth re-confirming against a wider vehicle sample at implementation time if the
  default proves consistently off for operators who don't override it.
- Fuel Finder API request/response shapes and the token endpoint were confirmed against the
  operator's own live registered credentials during implementation of issue #158 (not just docs,
  which proved to be generic boilerplate on several pages) — see the "Fuel price source" section
  above for the confirmed shapes. The batch-paginated, no-location-filter, national-dataset design
  was not anticipated during planning and materially changed the client's approach (client-side
  geocoding + distance ranking instead of a server-side postcode query).
- This spec deliberately performs the ADR 0017 loader generalisation ahead of the still-unimplemented
  Volvo spec — anyone later implementing Volvo's `VehicleProvider` should find the shared loader
  already in place and just add a third marker method, not redo this extraction.
- See ADR 0021 for why the new protocol is named `BehaviourProvider` rather than a
  feature-specific name, and the new "Dynamic Charging Threshold" section of `context.md` for the
  domain vocabulary this spec introduces.
- GitHub issue #124 (open, from the still-unimplemented Volvo spec) already covers this spec's
  loader-generalisation work with matching scope and acceptance criteria — this feature's issue
  list reuses #124 rather than duplicating it. Issue #131 (open, unimplemented) plans an
  `extensions/` subfolder reorganisation this spec's reference extension pre-adopts (see ADR 0021's
  amendment); #131 itself is untouched by this work.
