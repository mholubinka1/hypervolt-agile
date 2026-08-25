# Fix saints_fc: switch from football-data.org to TheSportsDB

## Problem Statement

`extensions/saints_fc.py` (shipped in PR #88) polls football-data.org's free tier for Southampton
FC fixtures. Before its config was rolled out to production, empirical verification against the
real API — prompted by the operator noticing a real Southampton FC vs West Ham United EFL Cup tie
was happening on the deployment day — found that football-data.org's free tier ("TIER_ONE")
returns `403 Forbidden` for the League/EFL Cup competition specifically:

```
GET /v4/competitions/FLC/matches → 403 "The resource you are looking for is restricted..."
```

The team-fixtures endpoint (`/v4/teams/340/matches`) doesn't surface this as an error — it just
silently omits any fixture from a competition the key can't see, so `saints_fc` reported "no match
today" for a day that genuinely had one. This isn't a code bug; it's a data-source coverage gap
that makes the extension's whole purpose — reacting to a real Southampton FC match day — fail
silently for any cup fixture, which is common (League Cup, FA Cup) for a Championship club.

## Solution

Switch `saints_fc`'s data source to [TheSportsDB](https://www.thesportsdb.com/)'s free v1 API,
verified empirically to correctly surface the same EFL Cup fixture that football-data.org missed.
TheSportsDB's free tier requires no signup — a public, shared test key (`"3"`) embedded directly
in the URL path grants access with no registration or payment. The `LedThemeProvider` interface
(`__init__`, `start`/`stop`/`resolve`) is unchanged — this is purely a data-source swap inside the
existing extension, verified against the interface documented in `app/hypervolt/led.py`.

## User Stories

1. As an operator running `saints_fc`, I want the extension to correctly detect Southampton FC
   cup fixtures (not just league fixtures), so that the LED theme actually reflects match days as
   promised, not just a subset of them.

## Implementation Decisions

**API shape change**: TheSportsDB's v1 API embeds its key directly in the URL path
(`https://www.thesportsdb.com/api/v1/json/<key>/...`), not as a request header — the `X-Auth-Token`
header and `_API_BASE_URL` constant both go away, replaced by a per-instance base URL built from
`self._api_key` at construction time.

**Two-endpoint poll, verified empirically**:
- `eventsday.php?d=<date>&t=<team>` — the date-scoped, team-filtered endpoint that looked like the
  obvious fit — was tested directly and found to **silently ignore its `t=` team filter**,
  returning unrelated events (unrelated-sport fixtures came back for a Southampton query). Not
  usable.
- `eventsnext.php?id=<team_id>` — correctly team-filtered (verified), returns upcoming events. This
  is what surfaced today's EFL Cup tie in initial testing, but only while it's still in the future
  — it won't necessarily include a match that has already kicked off or finished today.
- `eventslast.php?id=<team_id>` — correctly team-filtered (verified), returns recently completed
  events. Together with `eventsnext`, this covers a match at any point in its lifecycle: scheduled
  (in `eventsnext`), and completed (in `eventslast`) — satisfying the existing "any status, not
  just currently in progress" acceptance criterion from the original `saints_fc` spec.

Each poll cycle calls both endpoints and checks both response sets for an event whose
`dateEventLocal` (TheSportsDB's field for the fixture's date in the match venue's local timezone —
distinct from `dateEvent`, which is UTC) equals today's date in `_LOCAL_TZ` (`Europe/London`,
already imported from `common.constants.TIMEZONE`, unchanged from the current implementation).
Response shapes differ between the two endpoints: `eventsnext.php` returns `{"events": [...] |
null}`, `eventslast.php` returns `{"results": [...] | null}` — both must be defensively `or []`'d,
since an empty/no-fixture window can return `null` rather than `[]`.

Both requests happen inside the same `@retry()`-wrapped method (matching the existing
`common.decorator.retry` usage) — a failure in either request fails the whole poll attempt and
retries both, rather than tracking partial success across the two calls. Poll-failure handling is
otherwise unchanged: log a warning naming the exception, keep serving the last successfully-cached
`_match_date` rather than clearing it (a transient API blip must not flip a real match-day theme
off).

**`team_id` default changes value, not meaning**: TheSportsDB uses a completely different team-ID
space than football-data.org — Southampton FC is `134778`, not `340`. The config key name
(`team_id`) and its role (identify which team's fixtures to poll) are unchanged; only the default
value changes, from `340` to `134778`. `config.yml.template` and the extension's own module
docstring both explicitly note this is a TheSportsDB team ID, not football-data.org's, so a future
reader isn't confused by the ID jumping between slices.

**`api_key` becomes optional, default `"3"`**: TheSportsDB's shared public key (`"3"`) needs no
registration and is free forever; keeping `api_key` in the config schema (rather than hardcoding
`"3"` in the extension) costs nothing now and lets an operator drop in a personal TheSportsDB key
later (Patreon-based, dedicated rate limits instead of the shared pool) purely via a `config.yml`
edit — no code change. `is_null_or_empty` validation stays, guarding against an explicitly blank
string; there is no other format validation possible since TheSportsDB doesn't publish a fixed key
format.

**Unchanged**: `poll_interval_secs` validation (positive finite number), the `start()`/`stop()`
lifecycle (`asyncio.create_task(every(...))`, cancel-and-await on `stop()`), `resolve()`'s
cache-comparison logic (`_match_date` vs `now`'s date — still no I/O inside `resolve()`), the
`effect_name="saints_fc_matchday"` identity, and the alternating red/white 51-LED array. None of
these depend on the data-source provider.

## Testing Decisions

Same seam as the original implementation (`tests/extensions/test_saints_fc.py`): mock only the
real network boundary via `httpx.MockTransport`, exercising the extension's own request-building
and response-parsing code for real. Existing tests for `poll_interval_secs`/`api_key` validation,
`start()`/`stop()` lifecycle, poll-failure-keeps-cache, and the LED colour/effect assertions carry
over structurally unchanged (same behaviour, same seam) — only the mock response bodies and the
new two-endpoint request assertions change to match TheSportsDB's shape.

New test coverage specific to this change:
- A response from `eventsnext.php` with today's `dateEventLocal` → match-day theme.
- A response from `eventslast.php` (not `eventsnext.php`) with today's `dateEventLocal` → still
  match-day theme (covers the "already finished today" case `eventsnext` alone would miss).
- Neither endpoint has a today-dated event → `None`.
- One endpoint's response body has `"events": null` / `"results": null` (no fixtures in that
  window) → treated as empty, not a crash.
- The default `api_key` (`"3"`) and default `team_id` (`134778`) are used when omitted from
  `config:`.

`tests/hypervolt/test_led_load_shipped_extensions.py`'s existing integration test (loads the real
shipped `extensions/saints_fc.py` via the real `load_extensions` production path) needs its mocked
`httpx.AsyncClient` response updated to TheSportsDB's shape, but the test's purpose — proving the
real shipped file is a valid, loadable `LedThemeProvider` — is unchanged.

## Out of Scope

- Any change to the `LedThemeProvider` protocol, `ExtensionWrapper`, `load_extensions`, or
  `resolve_theme` — this is entirely internal to `extensions/saints_fc.py`.
- A personal TheSportsDB key — the shared free key is what's being deployed; upgrading later is a
  config-only change already supported by keeping `api_key` in the schema.
- Any other extension besides `saints_fc` — no other extensions exist yet.
- Re-deploying to the Pi — that resumes once this fix is merged; deployment steps 2–3 (directories,
  `led_effects/*.yaml`, and `saints_fc.py` copied to the Pi) already happened using the *old*
  football-data.org version of the file and will need re-copying once this lands.

## Further Notes

- Empirical verification (both the football-data.org 403 and TheSportsDB's correct EFL Cup
  fixture) was done via direct `curl` calls during this session, not assumed from documentation —
  football-data.org's own pricing/coverage pages don't clearly state which tier unlocks cup
  competitions, so the 403 was the only reliable signal.
- `eventsday.php`'s broken team filter is worth remembering if a future extension author reaches
  for it — it looks like the right endpoint from its name but doesn't do what it appears to.

## Update: Fixed-Time Daily Polling (added before merge)

### Problem Statement

The original slice above kept the previous 5-minute polling cadence, now checking "does Southampton
have a match today". That's ~288 TheSportsDB requests/day for a value that only meaningfully changes
once a day — fixture schedules don't move minute-to-minute. It also doesn't take advantage of the
data actually being knowable a day ahead: by checking "today" instead of "tomorrow", the LEDs only
ever light up on the day of the match itself, with no lead time.

### Solution

Poll once a day, at a fixed local time (default 23:00, operator-configurable), checking whether
Southampton have a fixture *tomorrow*. If found, that date is recorded as a confirmed match day;
`resolve()` lights the LEDs whenever `now`'s date matches any recorded date. On startup, the
extension *additionally* checks "today" specifically, exactly once — covering the case that started
this whole rewrite: a same-day deploy or restart on a day Southampton already have a fixture. This
bootstrap check is not repeated by any later poll, even if it fails outright — see Implementation
Decisions below.

### Implementation Decisions

**New scheduling primitive — `common.polling.daily_at(hour, minute, tz, task)`**: added
alongside the existing `every()` (interval-based, still used elsewhere e.g. `app/main.py` — unchanged)
rather than replacing it, since the two serve genuinely different scheduling models. `daily_at`
computes the next occurrence of `hour:minute` in the given `ZoneInfo`, sleeps until then, runs `task`,
then repeats — recomputing the target each iteration so each occurrence resolves against the correct
UTC offset for whatever date it falls on. This is *not* automatic from `ZoneInfo`-aware arithmetic
alone: Python's datetime subtraction special-cases two aware datetimes that share the identical
`tzinfo` object (which `_now` and `_target` always do here, since both come from the same `tz`
argument) by comparing naive field values directly rather than each side's resolved UTC offset — a
documented but easy-to-miss behaviour that silently drops or gains an hour across a DST transition if
not worked around. `daily_at` sidesteps it by normalising both sides to UTC (`.astimezone(timezone.utc)`)
before subtracting to compute the sleep duration, verified by a dedicated test
(`test_daily_at_computes_the_correct_sleep_duration_across_a_spring_forward_transition`) that fails
without the explicit UTC normalisation. Lives in `common/polling.py` since it's a generic scheduling
primitive, not saints_fc-specific, matching the existing precedent of `every()` and
`common.decorator.retry()` being shared utilities extensions consume.

**`poll_time` config key replaces `poll_interval_secs`**: an interval no longer means anything once
polling happens once a day at a fixed time. `poll_time` is a `"HH:MM"` 24-hour string (Europe/London,
matching the extension's existing timezone handling), default `"23:00"`, validated at construction via
`datetime.strptime(value, "%H:%M")` — non-string or malformed values raise `ValueError` at load time,
consistent with the existing `api_key`/prior `poll_interval_secs` validation pattern.
`poll_interval_secs` is removed from the schema entirely (not deprecated/aliased — no known operator
config depends on it yet, since this whole feature hasn't shipped to production).

**`_fetch_has_match_today(today: date)` generalises to `_fetch_has_match_on_date(target_date: date)`**:
identical two-endpoint (`eventsnext.php` + `eventslast.php`) logic, now callable for any date, not
just literally "today" — reused for both the startup/bootstrap "today" check and the daily "tomorrow"
check. `eventslast` is redundant for a future date (a fixture can't be "recently completed" before it
happens) but reusing the combined check avoids a second near-duplicate method for one harmless extra
HTTP request per day.

**`self._match_date: date | None` becomes `self._match_dates: set[date]`**: a single field can't
safely hold both "today confirmed" (from the bootstrap check) and "tomorrow confirmed" (from the
first scheduled poll) without one overwriting the other. No pruning of past dates — `resolve()` never
matches a stale date again regardless, and the memory cost (one `date` object per confirmed match day,
for as long as the process runs between restarts) is negligible.

**`start()` now does exactly one direct, awaited bootstrap check for "today" before scheduling the
recurring task** — a single call, not repeated by any later poll: `load_extensions`
(`app/hypervolt/led.py`) already awaits each extension's `start()` sequentially and already
catches/logs exceptions from it without crashing the app, so a blocking bootstrap check on load is
consistent with existing behaviour — no protocol or infra change needed (confirmed out of scope,
unchanged). The bootstrap check reuses the same failure handling as every other poll (catch, log a
warning, leave `_match_dates` unchanged) and relies solely on `common.decorator.retry`'s existing
per-request budget — if it fails outright (not just a transient blip absorbed by that retry), it is
never attempted again; every poll from then on only ever checks "tomorrow". Trade-off, accepted
explicitly: once a date is confirmed as a match day it is **never re-verified** before that date
arrives — a late postponement/reschedule discovered by TheSportsDB afterwards won't un-light the LEDs
or move the trigger. This is the accepted cost of dropping from ~288 to ~1–2 API calls/day.

### Testing Decisions

Same seam as before (`httpx.MockTransport` at the network boundary). New/changed coverage:
- `common/polling.py`'s new `daily_at()` gets its own direct tests (`tests/common/test_polling.py`,
  new file — `every()` currently has no dedicated test file, tested only indirectly via consumers, but
  new code added by this change is tested directly per this project's standards) covering: sleeps until
  the next occurrence of the target time, runs the task, then repeats; unhandled task exceptions don't
  kill the loop (matches `every()`'s own behaviour); the target time correctly skips to the next day
  when "now" is already past it today; a dedicated spring-forward DST test that caught a real bug
  during review (see Implementation Decisions) — the initial implementation slept for the wrong
  duration across a DST transition, and this test failed against it before the fix.
- `saints_fc.py`: `_fetch_has_match_on_date` behaves identically regardless of which calendar date is
  passed (existing eventsnext/eventslast/null-handling tests already cover this structurally, just
  re-target the date under test); a new startup-bootstrap test proves `start()` checks "today" once;
  a new daily-poll test proves the scheduled task checks "tomorrow"; a `poll_time` validation test
  (parametrized over non-string, malformed, and out-of-range values) mirrors the existing
  `api_key`/`poll_interval_secs` validation test style; a dedicated coexistence test proves a
  bootstrap-confirmed date and a later poll-confirmed date both resolve correctly at once — the entire
  reason `_match_date` became `_match_dates`, and a regression back to a single field would pass every
  other test in the file without this one.

### Out of Scope

- Re-verifying a previously-confirmed match date before it arrives (explicitly accepted trade-off
  above).
- Making the bootstrap "today" check itself independently retried beyond the normal per-request retry
  budget — if it fails outright (not just a transient blip), the extension simply won't discover a
  same-day match until the next `poll_time` and, from then on, will only ever check "tomorrow" again.
- Any other extension besides `saints_fc`, and any change to `LedThemeProvider`, `ExtensionWrapper`, or
  `load_extensions` (all still out of scope, per the original spec section above).
