# Issues: bugfix-saints-fc-thesportsdb

## Switch saints_fc from football-data.org to TheSportsDB — [#89](https://github.com/mholubinka1/hypervolt-agile/issues/89)

**Blocked by**: None

**User stories**: 1

### What to build

Rewrite `extensions/saints_fc.py`'s data-source layer to poll TheSportsDB's free v1 API instead
of football-data.org — verified empirically that football-data.org's free tier 403s on the League
Cup competition, silently missing cup fixtures. TheSportsDB's key is embedded in the URL path, not
a header. Each poll calls both `eventsnext.php?id=<team_id>` (upcoming) and
`eventslast.php?id=<team_id>` (recently completed) — `eventsday.php`'s team filter was tested and
found broken — and treats today as a match day if either response contains an event whose
`dateEventLocal` matches today's date in `Europe/London`. `team_id` default changes from `340`
(football-data.org) to `134778` (TheSportsDB), same config key, documented as provider-specific.
`api_key` becomes optional, defaulting to TheSportsDB's free shared key `"3"`. Everything else —
`start()`/`stop()` lifecycle, `poll_interval_secs` validation, poll-failure-keeps-cache,
`effect_name`, the red/white LED array — is unchanged.

### Acceptance criteria

- [x] Given `eventsnext.php` returns an event with today's `dateEventLocal`, when the poll runs,
      then a `LedTheme` with `effect_name="saints_fc_matchday"` and the alternating red/white
      51-element `leds` array is cached
- [x] Given `eventsnext.php` has nothing for today but `eventslast.php` returns an event with
      today's `dateEventLocal` (match already finished), when the poll runs, then the match-day
      theme is still cached — covers the case `eventsnext` alone would miss once kickoff passes
- [x] Given neither endpoint has an event dated today, when the poll runs, then `resolve(now)`
      returns `None`
- [x] Given either endpoint's response has `"events": null` or `"results": null` (no fixtures in
      that window), when the poll runs, then it's treated as no fixtures, not a crash
- [x] Given `api_key` is omitted from `config:`, when the extension is constructed, then it
      defaults to `"3"` (TheSportsDB's free shared key)
- [x] Given `team_id` is omitted from `config:`, when the extension is constructed, then it
      defaults to `134778` (Southampton FC's TheSportsDB ID)
- [x] Given a poll fails (network error, non-2xx response), when the poll runs, then a warning is
      logged and the previously-cached match-day value is left unchanged (unchanged from the
      existing behaviour)
- [x] `resolve(now)` still performs no I/O itself — cached-value read only (unchanged)
- [x] `tests/hypervolt/test_led_load_shipped_extensions.py`'s existing integration test (loads the
      real shipped file via the real `load_extensions` path) is updated to mock TheSportsDB's
      response shape and still passes
- [x] `config.yml.template`'s `saints_fc` example documents the new default `team_id` (134778)
      and notes it is a TheSportsDB ID, not football-data.org's

---

## Switch saints_fc to fixed-time daily polling — [#91](https://github.com/mholubinka1/hypervolt-agile/issues/91)

**Blocked by**: Switch saints_fc from football-data.org to TheSportsDB (#89)

**User stories**: 1

### What to build

Replace the 5-minute "is there a match today" polling with a once-daily, fixed-local-time poll
(default 23:00 Europe/London, configurable via a new `poll_time` "HH:MM" config key) that checks
whether Southampton have a match *tomorrow*. Add a new `common.polling.daily_at(hour, minute, tz,
task, on_tick=None)` scheduling primitive alongside the existing `every()` (unchanged, still used by
`app/main.py`). `_fetch_has_match_today(today)` generalizes to `_fetch_has_match_on_date(target_date)`
so the same two-endpoint (`eventsnext`+`eventslast`) logic works for any date. `start()` does one
extra, directly-awaited bootstrap check for "today" before scheduling the recurring daily task, so a
same-day deploy/restart doesn't miss a match already confirmed for that day — this bootstrap check
happens exactly once and is not retried by later polls if it fails outright. `self._match_date: date
| None` becomes `self._match_dates: set[date]` (a single field can't safely hold both a bootstrap
"today" result and the first daily "tomorrow" result without one overwriting the other); once a date
is confirmed, it is never re-verified before it arrives. `poll_interval_secs` is removed from the
config schema entirely, replaced by `poll_time`.

### Acceptance criteria

- [ ] Given `poll_time` is omitted from `config:`, when the extension is constructed, then it
      defaults to `"23:00"`
- [ ] Given `poll_time` is not a string, or is a string that isn't a valid `"HH:MM"` 24-hour time,
      when the extension is constructed, then a `ValueError` is raised
- [ ] Given `start()` is called, when it runs, then it directly awaits one check for whether there is
      a match *today* before scheduling the recurring daily task
- [ ] Given the recurring daily task fires, when it runs, then it checks whether there is a match
      *tomorrow* (not today) relative to that poll's local date
- [ ] Given a checked date (today at bootstrap, or tomorrow on a later poll) has a confirmed match,
      when `resolve(now)` is called with a `now` whose Europe/London date matches that recorded date,
      then the matchday `LedTheme` is returned
- [ ] Given both a bootstrap "today" match and a later "tomorrow" match have been confirmed at
      different times, when `resolve(now)` is called for either date, then the matchday theme is
      returned for both — confirming one recorded date cannot silently overwrite the other
- [ ] `common.polling.daily_at` sleeps until the next occurrence of the given local `hour:minute`,
      runs the task, then repeats; if "now" is already past that time today, the first run is
      scheduled for tomorrow; an unhandled exception from the task does not kill the loop (matching
      `every()`'s existing behaviour) — covered by new tests in `tests/common/test_polling.py`
- [ ] `common.polling.every` and its existing consumer (`app/main.py`) are unchanged
- [ ] `config.yml.template`'s `saints_fc` example documents `poll_time` (default `"23:00"`) and no
      longer references `poll_interval_secs`
- [ ] `tests/hypervolt/test_led_load_shipped_extensions.py`'s integration test still passes against
      the real shipped extension

---
