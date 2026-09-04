# Issues: feature-volvo-battery-level

## 1. Generalise the extension loader to support multiple provider protocols

**GitHub issue**: #124

**Blocked by**: None

**User stories**: 5

### What to build

Extract the protocol-agnostic parts of `app/hypervolt/led.py`'s extension-loading machinery
(dynamic module loading via `importlib`, the `sys.modules` registration workaround, the
`extensions_dir` path-traversal guard, `load_extensions`, and the `ExtensionWrapper`
isolation/dedup-logging wrapper) into a shared location, parameterised by the provider's marker
method name (currently hardcoded to `resolve`). `hypervolt/led.py` keeps only what's genuinely
LED-specific — the `LedTheme` dataclass, theme window resolution — and a thin LED-specific
wrapper around the shared loader. See ADR 0017 for the full rationale.

This is pure prefactoring: no behavioural change to LED Theme Extensions. `extensions/saints_fc.py`
and all other existing LED extension config continues to work identically.

### Acceptance criteria

- [ ] All existing LED extension tests pass unmodified against the refactored code
- [ ] The shared loader is parameterised by marker method name, not hardcoded to `resolve`
- [ ] A new test proves the shared loader can load and wrap a provider identified by a
      *different* marker method (a fake protocol distinct from `LedThemeProvider`), confirming
      it is genuinely protocol-agnostic and not just renamed LED-specific code
- [ ] No change to `config.yml`, `--extensions-dir` behaviour, or any LED-visible behaviour

---

## 2. VehicleProvider protocol and coordinator battery-target gating

**GitHub issue**: #125

**Blocked by**: #124

**User stories**: 1, 2, 3, 4

### What to build

Define the `VehicleProvider` protocol (`__init__(config)`, `async get_battery_status() ->
VehicleStatus | None`, optional `start()`/`stop()` lifecycle hooks) and the `VehicleStatus`
dataclass (battery percentage, `reported_at`, `connected` flag), loaded through the generalised
extension mechanism from issue #124. Add `vehicles: list[ExtensionEntry] = []` to `AppConfig`.

In `ScheduleCoordinator`, each poll cycle: call `get_battery_status()` on every loaded vehicle
extension; filter to `connected is True` and `reported_at` within the last 30 minutes; exactly
one match is the active vehicle. Zero or multiple matches is ambiguous/unknown — no gating, log
a deduplicated warning on the multiple-match case. If the active vehicle has a configured
`target_charge_percent` and its battery is at or above it, force `_lock_control()`'s decision to
locked regardless of `_should_unlock()` — this is additive to the existing `car_plugged`/
`release_state` gates, not a replacement. Schedule building/pushing is unaffected either way.

No real manufacturer extension exists yet at this point — prove the mechanism with an in-test
fake `VehicleProvider`.

### Acceptance criteria

- [ ] `VehicleProvider` protocol and `VehicleStatus` dataclass are defined and load through the
      shared extension mechanism
- [ ] Exactly one connected vehicle (within the 30-minute staleness window) is selected as active
- [ ] Zero connected vehicles → no gating, scheduler behaves as today
- [ ] More than one connected vehicle simultaneously → no gating, a warning is logged once (not
      every cycle) for a persistently ambiguous state
- [ ] A stale reading (reported_at older than 30 minutes) is treated the same as unavailable
- [ ] Active vehicle with no `target_charge_percent` configured → monitored/logged only, never
      gates lock state
- [ ] Active vehicle at or above its configured target → charger is forced locked even with an
      already-active, currently-unlocked session
- [ ] Schedule building and pushing continue normally regardless of the lock-gate outcome

---

## 3. Volvo OAuth 2.0 + PKCE authentication and bootstrap

**GitHub issue**: #126

**Blocked by**: #125

**User stories**: 6, 7, 8 (auth portion)

### What to build

`app/volvo/auth.py`: PKCE code verifier/challenge generation, authorization-code and
refresh-token grant exchanges against Volvo's identity provider
(`https://volvoid.eu.volvocars.com`), and token file persistence to an explicit
`token_store_path` (required per vehicle config entry — no default, so two Volvo entries never
collide). Every refresh call re-persists whatever `refresh_token` comes back, even if unchanged
(defends against rotation without needing to know for certain that it happens).

`scripts/volvo_auth.py`: one-time bootstrap, outside `app/`, never invoked by the scheduler.
Prints the Volvo authorization URL; prompts the operator to paste back the `code` query
parameter from wherever their browser's consent redirect landed (manual-paste flow — no local
callback listener, works identically whether run locally or over SSH on a headless Pi). Reads
`client_id`/`client_secret` and writes the resulting tokens to the configured
`token_store_path`.

### Acceptance criteria

- [ ] PKCE code verifier/challenge are generated correctly (S256)
- [ ] `scripts/volvo_auth.py` prints a valid authorization URL and accepts a pasted-back code,
      with no local HTTP server involved
- [ ] Successful token exchange writes access token, refresh token, and expiry to
      `token_store_path`
- [ ] A 401 on an API call (tested at the auth-module level) triggers exactly one refresh
      attempt before giving up
- [ ] Every refresh call rewrites `token_store_path` with the latest `refresh_token`, even when
      its value is unchanged from before
- [ ] Two vehicle entries with different `token_store_path` values never read or write each
      other's token file

---

## 4. Volvo Connected Vehicle API REST client

**GitHub issue**: #127

**Blocked by**: #126

**User stories**: 6, 8

### What to build

`app/volvo/client.py`: REST calls against Volvo's Connected Vehicle API using the access token
and `vcc_api_key` header — fetching battery level, connection/charging status, and the
account's vehicle list (for VIN auto-discovery when `vin` is not configured; log a warning if
the account has more than one vehicle and none is specified). Full error handling: 401 (delegate
one refresh attempt to `app/volvo/auth.py`, then give up), 403 (log naming
`scripts/volvo_auth.py` and likely missing/incorrect scopes), 404 (log naming the
configured/discovered VIN and likely mismatch), 429 (exponential backoff, minimum 60s wait,
capped at 3 retries), 5xx/network (log a warning, skip this call).

### Acceptance criteria

- [ ] Battery level and connection status are fetched and returned as a typed result
- [ ] VIN auto-discovery works when `vin` is absent from config, with a warning logged for a
      multi-vehicle account
- [ ] 401 triggers exactly one refresh-and-retry via the auth module; a second failure is caught
      and logged, not raised
- [ ] 403 and 404 each log their documented, distinct remediation hint
- [ ] 429 backs off exponentially with a 60s minimum wait and stops after 3 retries
- [ ] 5xx and network errors are caught, logged as a warning, and never propagate as an
      exception out of the client

---

## 5. Volvo VehicleProvider extension adapter

**GitHub issue**: #128

**Blocked by**: #127

**User stories**: 6, 7, 8

### What to build

`extensions/volvo.py`: the thin `VehicleProvider` adapter composing `app/volvo/auth.py` and
`app/volvo/client.py`. Owns a background polling task (started in `start()`, cancelled in
`stop()`) on a 5-minute cadence, caching the latest `VehicleStatus` so `get_battery_status()`
returns instantly every coordinator cycle without ever awaiting live I/O — the same
cached-polling pattern LED extensions already use (ADR 0005) for an identical rate-limit
problem. Any exception anywhere in the poll is caught and logged inside the extension itself, so
nothing reaches the shared `ExtensionWrapper` as an unexpected failure needing its own handling
beyond what issue #124 already provides.

This is the slice that makes Volvo end-to-end usable: registering `vehicles: [{name: volvo,
config: {...}}]` in `config.yml` now produces real battery-target gating in production.

### Acceptance criteria

- [ ] `extensions/volvo.py` implements `VehicleProvider` and loads via the same
      `--extensions-dir` mechanism LED extensions use
- [ ] The background poll runs every ~5 minutes independent of the coordinator's ~10s cycle
- [ ] `get_battery_status()` never awaits a live API call — always returns from cache
- [ ] A failing poll (any of the error cases from issue #127) results in `get_battery_status()`
      returning `None` or a stale cached value, never raising
- [ ] `reported_at` on the returned `VehicleStatus` is the vehicle's own last-transmission time,
      not the poll time
- [ ] End-to-end: with valid Volvo credentials configured, the app logs battery level each poll
      and locks the charger once the configured target is reached

---

## 6. Config schema, stale stub removal, and documentation

**GitHub issue**: #129

**Blocked by**: #128

**User stories**: None directly (supporting work for all)

### What to build

Remove the stale commented-out `Manufacturer`/`Volvo` stub in `app/config.py` (its
`key`/`username`/`password` shape predates and doesn't match this OAuth 2.0 + PKCE design).
Document the `vehicles:` config block in `config/config.yml.template`, including the Volvo
extension's own config keys (`client_id`, `client_secret`, `vcc_api_key`, optional `vin`,
optional `target_charge_percent`, required `token_store_path`) and a worked multi-vehicle
example. Update the README with a short section on registering a vehicle extension and running
`scripts/volvo_auth.py`.

### Acceptance criteria

- [ ] The stale `Manufacturer`/`Volvo` stub and its TODO comment are removed from `app/config.py`
- [ ] `config/config.yml.template` documents the `vehicles:` block with a Volvo example,
      including a two-vehicle (multi-car household) example
- [ ] README documents the one-time `scripts/volvo_auth.py` bootstrap step
- [ ] `FEATURES.md` Feature 16 marked complete and moved to `FEATURES_ARCHIVE.md` (local-only,
      not committed)

---
