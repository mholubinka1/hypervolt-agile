# saints_fc: derive a fixture's local date from its kick-off timestamp

## Problem Statement

On 2026-11-07 Southampton were playing Preston North End (15:00 kick-off) and the charger's LED strip
never lit. The extension loaded cleanly, polled without error, and the shipped file matched `main` —
it simply never recorded the fixture.

TheSportsDB now returns `dateEventLocal` and `strTimeLocal` as `null` for this fixture. The data that is
present is `dateEvent` (`2026-11-07`, the UTC date) and `strTimestamp` (`2026-11-07T15:00:00`, UTC, no
offset). `saints_fc` selects fixtures by `dateEventLocal == target date` only, so the event is dropped
before its kick-off is ever parsed, no match day is recorded, and `resolve()` returns nothing. Nothing is
logged because nothing failed.

## Solution

A fixture's local match date is derived from its kick-off instant (the parsed `strTimestamp`, converted to
Europe/London). When there is no parseable timestamp, fall back to `dateEventLocal`, then `dateEvent`.
A fixture TheSportsDB describes with null local fields is then recorded and lights the strip in the usual
match window (30 minutes before kick-off to 3 hours after).

## User Stories

1. As the operator, I want a match day to be detected even when TheSportsDB leaves `dateEventLocal` null,
   so that the strip lights for the match instead of silently staying dark.
2. As the operator, I want a fixture's date taken from its kick-off in Europe/London, so that a BST
   evening kick-off is filed under the correct local day rather than the UTC date.
3. As the operator, I want fixtures that do populate `dateEventLocal` to behave exactly as before, so that
   the fix does not regress the data shape that worked.
4. As the operator, I want a fixture with no timestamp and no local date to still be recorded via
   `dateEvent`, so that it is at least fallback-lit for the day.

## Implementation Decisions

- Date selection moves from a raw `dateEventLocal` string comparison to a single "local date of this
  event" derivation, used by the fixture filter in the per-date fetch.
- Precedence: kick-off from `strTimestamp` converted to the charger-local zone; else `dateEventLocal`;
  else `dateEvent`. The kick-off parser already yields an aware instant for both the space-separated and
  ISO `T` timestamp forms (a naive value is read as UTC), so the `T` form needs no new parsing.
- The kick-off list recorded for the date is unchanged; only which date an event belongs to changes.
- No change to the poll cadence, the bootstrap check, the match window, or the extension interface.

## Testing Decisions

- Single seam: the existing `httpx.MockTransport` seam in `tests/extensions/test_saints_fc.py`, driving
  `start()` / `_poll_once()` / `resolve()` against realistic TheSportsDB payloads. No new seams.
- Tests assert external behaviour (is the date recorded, does `resolve()` light inside the window).
- New cases: (a) the real 2026-11-07 payload shape — null local fields, `T`-form `strTimestamp` — is
  recorded and lights in-window; (b) a late-evening UTC timestamp whose London date differs from the UTC
  date lands on the London date; (c) no timestamp and no `dateEventLocal`, only `dateEvent`, records the
  date with an empty kick-off list.
- Existing `dateEventLocal`-based tests must keep passing unchanged.

## Out of Scope

- Poll cadence, window lengths, and the `LedThemeProvider` interface.
- Alerting when a poll succeeds but finds zero fixtures.
- Deploying to the Pi (copy `extensions/saints_fc.py` and restart the container) — done after merge.

## Further Notes

- Extensions are never baked into the image, so the fix reaches the Pi only via a manual copy to the
  bind-mounted extensions directory plus a container restart. The startup bootstrap check then picks up a
  same-day match.
