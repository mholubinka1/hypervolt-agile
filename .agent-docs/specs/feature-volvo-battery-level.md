# Multi-Manufacturer Vehicle Battery Monitoring and Target-Charge Gating

## Problem Statement

Today the scheduler charges purely on price: it builds sessions from the cheapest available
Agile windows and never knows how full the car's battery actually is. An operator with an EV
that reports its own battery level (starting with Volvo, via Volvo's Connected Vehicle API) has
no way to stop charging once the battery reaches a sensible target — the car keeps charging
through every scheduled cheap window even after it no longer needs the energy, which both wastes
money on marginal charging and is worse for long-term battery health than stopping at a
configured target (e.g. 80%) most of the time.

A household may also have more than one EV sharing a single charger. There is currently no way
to register more than one vehicle, and no way to know which of several registered vehicles is
the one actually plugged in right now — the Hypervolt charger itself only reports a bare
`car_plugged` boolean, with no vehicle identity.

Volvo cannot be hardcoded as the only manufacturer: this codebase already has a proven pattern
(LED Theme Extensions, Feature 19) for letting operators register manufacturer/team-specific
logic as a self-contained, dynamically-loaded module, isolated from the app's core so it can't
take core scheduling down if it misbehaves. Vehicle battery monitoring should use that same
pattern rather than a bespoke integration bolted directly into the scheduler.

## Solution

Generalise this codebase's existing extension-loading mechanism (currently LED-only) so it also
supports **Vehicle Provider extensions** — operator-registered modules under `extensions/`, one
per configured vehicle, each reporting that vehicle's battery percentage, how fresh the reading
is, and whether that vehicle currently reports itself as connected/charging. Ship Volvo as the
first (reference) implementation, using OAuth 2.0 + PKCE against Volvo's own identity provider.

Each poll cycle, the coordinator asks every registered vehicle extension for its current status
(a fast, cached read — extensions own their own polling cadence, exactly like LED extensions
already do). Exactly one vehicle reporting itself connected is treated as "the" active vehicle
for that cycle. If that vehicle has a configured target charge percentage and its battery is at
or above that target, the coordinator forces the charger locked — even interrupting an
already-active session — until the car is unplugged and something changes. Zero vehicles
reporting connected, more than one reporting connected simultaneously, or a reading that's
missing/stale, are all treated identically: **unknown**, and the scheduler falls back to normal
price-only behaviour exactly as if this feature didn't exist. The car is never stranded
uncharged because of a vehicle API problem.

## User Stories

1. As an operator with a target-percentage EV, I want charging to stop once my car reaches a
   configured battery target, so that I don't waste money and battery health charging past the
   point I actually need.
2. As an operator, I want that stop to take effect immediately (even mid-session), so that the
   car never charges meaningfully past its target regardless of what the price-optimal schedule
   says.
3. As an operator with more than one EV sharing a charger, I want the app to figure out which
   car is currently plugged in from each car's own reported connection state, so that I don't
   have to manually tell it which vehicle is home.
4. As an operator, I want the scheduler to behave exactly as it does today whenever vehicle data
   is missing, stale, or ambiguous (no cars or multiple cars reporting connected at once), so
   that a battery-API hiccup or an unusual household state never leaves the car un-chargeable.
5. As a developer, I want vehicle-manufacturer support to be a pluggable extension, not
   hardcoded Volvo-specific logic in the scheduler, so that a future Tesla/Polestar/etc.
   extension can be added by dropping in a new file, the same way LED theme extensions work
   today.
6. As a Volvo driver, I want my car's battery level and connection state fetched from Volvo's
   Connected Vehicle API using my own developer-portal credentials, so that the app can act on
   real data without Volvo's shared-app rate limits or terms-of-service restrictions being a
   problem.
7. As an operator setting up Volvo for the first time, I want a one-time bootstrap script that
   walks me through Volvo's OAuth consent flow without needing a browser on the machine running
   the scheduler, so that this works on a headless deployment (e.g. a Raspberry Pi).
8. As the scheduler, I want any Volvo API failure — network, 4xx, 5xx, or an expired/invalid
   token — fully isolated to that one vehicle extension, so that it can never affect charger
   control, LED themes, or any other vehicle extension.

## Implementation Decisions

### Generalising the extension mechanism (ADR 0017)

- The dynamic module-loading machinery currently in `app/hypervolt/led.py`
  (`_load_provider_class`, the `sys.modules` registration workaround, the `extensions_dir`
  path-traversal guard, `load_extensions`) and the isolation/dedup-logging wrapper
  (`ExtensionWrapper`) are protocol-agnostic already — only "which method identifies a valid
  provider class" (`hasattr(cls, "resolve")`) is LED-specific — and `ExtensionWrapper` itself
  hardcodes `.resolve()`/`.resolve_fallback()` as the methods it dispatches through for
  isolation/dedup. Extract the generic parts into a shared location, parameterised by both the
  provider's marker method name (how the loader identifies a valid class) and the wrapper's own
  dispatch method (what it actually calls and isolates), so both `LedThemeProvider` (`resolve`)
  and the new `VehicleProvider` (`get_battery_status`) load through the same code, the same
  `extensions/` directory, and the same `--extensions-dir` CLI flag. `hypervolt/led.py` keeps
  only what's LED-specific (the `LedTheme` dataclass, theme window resolution) and a thin
  LED-specific wrapper around the shared loader.
- No change to LED behaviour, config, or existing extension files (`saints_fc.py` continues to
  work unmodified) — this is a pure prefactor making the mechanism reusable, not a rewrite.

### VehicleProvider protocol

- New protocol (mirrors `LedThemeProvider`'s shape): `__init__(self, config: dict) -> None`,
  `async def get_battery_status(self) -> VehicleStatus | None`, with the same optional
  `start()`/`stop()` lifecycle hooks LED extensions already use for owning their own background
  polling task and cleanup. `None` means "no status available this cycle" (extension not yet
  polled, or its own poll is currently failing).
- New `VehicleStatus` dataclass: battery percentage, the reading's `reported_at` timestamp (the
  vehicle's own last-transmission time, not the poll time — propagate it, don't substitute
  `now()`), and a `connected` flag (that vehicle's own report of whether it's currently
  plugged in/charging — not derived from the Hypervolt charger's `car_plugged`, which carries no
  vehicle identity).

### Coordinator: active-vehicle resolution and target-charge gating

- Each poll cycle, the coordinator calls `get_battery_status()` on every loaded vehicle
  extension (cheap, cached — same pattern as LED's `resolve()`). Filter to statuses where
  `connected is True` and `reported_at` is within a 30-minute staleness window.
- **Exactly one** match → that is the active vehicle for this cycle.
- **Zero or more than one** match → ambiguous/unknown. Log a warning on the multiple-match case,
  deduplicated by a new coordinator-level "last logged ambiguous state" check (analogous in
  spirit to how `ExtensionWrapper` dedupes a single extension's repeated identical failures, but
  necessarily a new mechanism — this is a cross-extension comparison across multiple providers'
  results each cycle, not one provider's own repeated exception) so a persistently ambiguous
  household doesn't spam logs every ~10s. No gating either way — the scheduler behaves exactly
  as it does today.
- If the active vehicle has a configured `target_charge_percent` and its battery is at or above
  that target: force the lock decision inside `_lock_control()` to locked, regardless of
  `_should_unlock()`. This is a new override that runs only when `_can_push()` already permits
  lock control (i.e. `car_plugged` and `release_state` already allow it) — it does not touch
  `_can_push()` itself, and does not replace either existing gate. It affects **lock state
  only**; schedule building and pushing (`_apply_charging_schedule`) continue to run normally on
  the price-optimal windows, since a locked charger simply can't act on them — this mirrors how
  the app already separates "what the ideal schedule would be" from "whether the charger is
  currently allowed to act on it." No target configured on the active vehicle → no gating at
  all, that vehicle is monitored/logged only.
- This gate re-evaluates fresh every cycle from the live battery reading — no persisted "target
  reached" flag is needed. Unplugging and replugging (or the battery genuinely dropping) is the
  natural reset, exactly like the existing `release_state`/re-plug handling elsewhere in this
  scheduler.

### Config schema

- New `vehicles: list[ExtensionEntry] = []` on `AppConfig` — reuses the exact same
  `ExtensionEntry` (`name`, `config: dict`) shape LED extensions already use, loaded from the
  same `extensions/` directory via the same `--extensions-dir` flag (see ADR 0017). Supports any
  number of registered vehicles (household with multiple EVs).
- Remove the stale commented-out `Manufacturer`/`Volvo` config stub in `app/config.py`
  (`# TODO: re-add when implementing Volvo support`) — its shape (`key`, `username`, `password`)
  doesn't match this design (OAuth 2.0 + PKCE, not a password grant) and predates it.
- Volvo's own `config:` dict (defined and validated by the Volvo extension itself, not by core
  `app/config.py`, consistent with "every extension operates from its own config block in total
  isolation" per ADR 0006): `client_id`, `client_secret`, `vcc_api_key` (all from the operator's
  own free Volvo developer-portal app registration — a shared app credential cannot be used,
  per Volvo's terms and its 10,000 calls/day quota), optional `vin` (auto-discovered from the
  account's vehicle list if absent; log a warning if the account has more than one vehicle and
  none is specified), optional `target_charge_percent` (omit = monitored/logged only, no
  gating), and a required `token_store_path` (see Token storage below).

### Volvo extension: architecture

- Reference implementation lives at `extensions/volvo.py` (the thin `VehicleProvider` adapter —
  owns the background 5-minute polling task per vehicle, matching the `start()`/`stop()`
  cached-polling pattern LED extensions already use for the identical rate-limit problem), backed
  by a proper `app/volvo/` package for the substantial OAuth/REST logic: `app/volvo/auth.py`
  (PKCE code generation, token exchange/refresh, token file persistence) and
  `app/volvo/client.py` (the Connected Vehicle API REST calls: battery level, connection status,
  vehicle list for VIN auto-discovery). A shipped extension composing baked-in `app/` framework
  code is the same relationship `extensions/saints_fc.py` already has with `app/common/*` and
  `app/hypervolt/led.py` — ADR 0006's isolation requirement is about operator-supplied
  extensions not needing an image rebuild, not about every extension being a single file.
- `scripts/volvo_auth.py` — one-time bootstrap, outside `app/`, never invoked by the scheduler
  (same category as the existing LED calibration script): generates the PKCE code
  verifier/challenge, prints the Volvo authorization URL, and prompts the operator to paste back
  the `code` query parameter from wherever their consent redirect landed. See Headless OAuth
  flow below for why this is a manual-paste flow rather than a local callback listener.
- Discovery/token endpoint: Volvo's identity provider at `https://volvoid.eu.volvocars.com`
  (confirmed from Volvo's own official `oauth2-code-flow-sample`), standard OIDC
  authorization-code + PKCE (S256) and refresh-token grants.

### Headless OAuth flow

- A local callback HTTP server only completes automatically if the browser doing the consent
  step runs on the same machine as the listener — fragile to rely on over SSH into a headless
  Pi. Instead, `volvo_auth.py` prints the authorization URL and the operator opens it on *any*
  device with a browser (phone, laptop). After consenting, the browser is redirected to the
  registered `redirect_uri` and fails to load it (nothing is listening there) — but the `code`
  query parameter is visible in the browser's address bar. The operator copies that value and
  pastes it into the terminal running the script, which then exchanges it for tokens. No
  listener, no network reachability assumptions between the browser device and the script's
  device.

### Token storage

- Tokens are **not** stored in `config.yml`. Volvo's own official sample re-captures
  `refresh_token` after every refresh call — the standard defensive pattern for a token that may
  rotate on a Keycloak-style OIDC provider (`volvoid.eu.volvocars.com`, confirmed via Volvo's
  official `oauth2-code-flow-sample` on GitHub). Whether Hypervolt's own auth provider behaves
  the same way was not checked during this session and isn't relevant to this decision — this
  choice rests solely on Volvo's own confirmed sample behaviour. Persisting a possibly-rotating
  value into `config.yml` would require a comment-preserving YAML writer (`ruamel.yaml`, a new
  dependency) and would turn a previously read-only startup file into runtime-mutable state.
- Instead, each vehicle's `config:` block requires an explicit `token_store_path` — a flat,
  gitignored JSON file the runtime reads and rewrites freely on every refresh. This is required
  (not defaulted) specifically so two vehicle entries using the same extension (e.g. two Volvo
  cars) never collide on the same file. `volvo_auth.py`'s bootstrap writes the initial tokens to
  the same path the running app will later use.

### Error handling (unchanged from the original plan, now scoped to the extension)

- **401** — attempt one token refresh via `app/volvo/auth.py`; if that also fails, log and
  report unavailable for this cycle (surfaces as `get_battery_status()` returning `None`, or a
  cached-stale status if one exists — either way this vehicle drops out of the "connected"
  filter and the scheduler falls back to price-only behaviour).
- **403** — log naming `scripts/volvo_auth.py` and the likely cause (missing/incorrect scopes).
- **404** — log naming the configured/discovered VIN and the likely cause (wrong VIN or account
  mismatch).
- **429** — exponential backoff, minimum 60s wait, capped at 3 retries, inside the extension's
  own background poll — never blocks the coordinator's ~10s cycle (same non-blocking guarantee
  ADR 0005 already established for LED extensions).
- **5xx / network** — log a warning, skip this poll, resume on the next 5-minute interval.
- All of the above are caught and logged by the extension itself; nothing propagates out of
  `get_battery_status()` as an exception — consistent with how `ExtensionWrapper` already
  isolates and dedup-logs LED extension failures, generalised in ADR 0017 to cover this
  provider kind too.

## Testing Decisions

- **VehicleProvider protocol + active-vehicle resolution + target-gating logic**: pure
  logic, no I/O — test with fake in-test `VehicleProvider` implementations (returning
  hand-built `VehicleStatus` values) wrapped in the shared extension wrapper, exactly the
  pattern `tests/schedule/test_coordinator.py` already uses for LED extensions (a real
  lightweight provider instance with its internal state seeded directly, no mocking of the
  provider itself). Cases: exactly-one-connected selects that vehicle; zero-connected and
  multiple-connected both fall back to unmonitored; a connected vehicle with no
  `target_charge_percent` never gates; a connected vehicle at/above target forces locked even
  with an active session; a stale reading (older than 30 minutes) is treated as unavailable.
- **Generalised extension loader**: extend the existing LED extension-loader tests
  (`tests/hypervolt/test_led_load_extensions.py`, `test_led_load_shipped_extensions.py`) to
  confirm LED behaviour is unchanged post-refactor, plus new equivalent tests loading a fake
  `VehicleProvider` module to prove the same loader correctly identifies and wraps a different
  marker method.
- **`app/volvo/auth.py` and `app/volvo/client.py`**: seam is `httpx.MockTransport` against a
  real `httpx.AsyncClient`, the same convention already used in `tests/extensions/test_saints_fc.py`
  for its own httpx-based extension — no separate mocking library. Cases per the Error handling
  section above (401 triggers one refresh attempt, 403/404 log the documented remediation
  hints, 429 backs off with the 60s/3-retry bounds, 5xx/network is caught and skipped), plus
  VIN auto-discovery (single vehicle, multiple vehicles with a warning), and refresh-token
  persistence (the token file is rewritten with whatever `refresh_token` came back, even when
  unchanged). Also required, matching an existing proven precedent in this exact area —
  `tests/extensions/test_saints_fc.py`'s test guarding against an API key embedded in a request
  URL leaking into a logged exception message: a case proving that a failed request never leaks
  `client_id`, `client_secret`, `vcc_api_key`, the access token, or the refresh token into a log
  message,
  since httpx's own exception messages embed the full request URL and this client's credentials
  are materially more sensitive than `saints_fc`'s shared public test key.
- **`extensions/volvo.py`**: the thin adapter's own logic (background task lifecycle,
  caching) follows the same test shape already proven for `saints_fc.py`'s `start()`/`stop()`
  task management — no new pattern needed.
- No test coverage is meaningful for `scripts/volvo_auth.py` itself (interactive, one-time,
  requires a real Volvo consent flow) — matches the existing precedent for
  `scripts/calibrate_leds.py` (Feature 22): verification is manual, by a real operator running
  it once.

## Out of Scope

- Any UI/dashboard for battery level — this remains a logged value, same as every other piece
  of charger state in this app.
- Any manufacturer beyond Volvo in this spec — the extension mechanism is generalised to make a
  future Tesla/Polestar/etc. extension possible, but no second manufacturer is being built now.
- Deriving vehicle identity from the Hypervolt charger session itself (e.g. matching a VIN
  against charger telemetry) — the charger exposes no such data; detection is entirely via each
  vehicle's own self-reported connection state.
- Changing `release_state`/`car_plugged` gating semantics — the new target-reached override is
  additive, alongside those existing gates, not a replacement for either.
- A configurable staleness window — 30 minutes is fixed for this spec; making it configurable
  can be a follow-up if it proves too tight or too loose in practice.

## Further Notes

- This spec supersedes the single-manufacturer, log-only design originally sketched in
  `FEATURES.md` Feature 16 — scope grew twice during this planning session: first from
  observational-only to actively gating charging at a target percentage, then from
  single-vehicle to a pluggable multi-manufacturer/multi-vehicle extension architecture.
- Exact Volvo API details not confirmed during this planning session (specific scope names for
  energy/battery read access; the exact endpoint and field names for a vehicle's
  connection/charging status, as distinct from its battery level) are implementation-time
  lookups against Volvo's Connected Vehicle API reference docs, not planning blockers — the
  same treatment already given to other not-yet-confirmed wire formats elsewhere in this
  backlog (e.g. the descoped Feature 18).
- ADR 0017 records the extension-loader generalisation decision.
