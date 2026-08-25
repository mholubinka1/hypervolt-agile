> Work complete — PR ready to merge.

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
