# Stand down quietly while the charger is in Hypervolt Ultra mode

## Problem Statement

When the charger is switched to Hypervolt Ultra (the Axle energy-provider mode), the app logs a
`schedules.get parse error: Failed to parse session` warning on every poll, forever. The charger
reports Ultra's sessions as `session_type: "unique"` with ISO-8601 UTC datetimes and no `days`
key, which the app's recurring-session parser rejects. The app then treats the charger as having
no schedule, keeps attempting schedule pushes that the charger refuses ("Cannot set a hypervolt
schedule when an energy provider is active: Axle"), and keeps running lock control, which can lock
the charger out of the very windows Ultra wants to charge in.

## Solution

The app recognises Ultra from the schedule the charger reports (`type: "axle"`) and stands down:
it stops parsing the provider's sessions, stops pushing schedules, and stops lock/unlock control.
It logs one INFO line when Ultra becomes active and one when it ends, and nothing per poll. LED
themes and charger-state refresh keep working. When the charger leaves Ultra, normal scheduling
resumes on its own.

## User Stories

1. As an operator testing Ultra, I want no parse warnings in the log while Ultra is active, so
   that real problems are not buried in noise.
2. As an operator, I want one INFO line when the app detects Ultra and one when it ends, so that I
   can see from the log when the app stood down and resumed.
3. As an operator, I want the app to stop pushing Agile schedules while Ultra is active, so that
   it does not repeatedly attempt writes the charger will refuse.
4. As an operator, I want the app to stop locking and unlocking the charger while Ultra is active,
   so that Ultra can charge in its own windows.
5. As an operator, I want Ultra detected even when the provider currently has zero sessions, so
   that standing down does not depend on the session list.
6. As an operator, I want the app to resume scheduling and lock control automatically when the
   charger returns to a normal mode, so that I do not have to restart anything.
7. As an operator, I want LED themes and state refresh to keep working during Ultra, so that the
   rest of the app is unaffected.
8. As an operator on `schedule` or `octopus` mode, I want behaviour unchanged, including the
   existing handling of recurring sessions.

## Implementation Decisions

- **Activation mode** gains an `ultra` value. It is set when `schedules.get` reports an applied
  schedule whose `type` is `"axle"` (regardless of `enabled` and of its session list). The check
  runs before any session parsing.
- While `ultra`, the provider's sessions are not parsed into the app's recurring session model.
  The delta carries `activation_mode=ultra` and an empty current schedule.
- A genuinely malformed recurring session in a non-Ultra schedule still produces the existing
  warning; only the Ultra case is exempt.
- The coordinator's "can push" gate returns false while the charger's activation mode is `ultra`.
  That gate already covers both schedule pushes and lock control, so no further change is needed
  there.
- INFO lines on entering and leaving Ultra are emitted on the transition only, not each poll.
- The startup `clear_schedule` is untouched: the charger refuses it under Axle and the app already
  logs that as a warning once per start.
- The domain glossary's Activation mode entry is updated to list `ultra` (done during design).
- Detection by `type == "axle"` was chosen from a captured live payload (`type: "axle"`,
  `enabled: true`, `is_default: false`, two `unique` boost sessions plus a mirrored
  `sessions_close` list) rather than inferred from session shape.

## Testing Decisions

- Test external behaviour: given an incoming `schedules.get` message and charger state, assert the
  emitted state delta, the log lines, and the calls made on the charger client. No assertions on
  private parsing helpers.
- Protocol/websocket seam: feed the captured Axle payload and assert activation mode `ultra`, no
  sessions, no warning, one INFO on entering; feed a normal recurring payload afterwards and assert
  one INFO on leaving and the normal mode restored; assert an Axle payload with an empty session
  list is still detected; assert a malformed recurring payload still warns.
- Coordinator run-loop seam: with activation mode `ultra`, assert no schedule apply and no
  lock/unlock calls; assert both resume once the mode changes back.
- Prior art: existing websocket tests for protocol messages and existing coordinator tests with a
  fake charger client.

## Out of Scope

- Modelling, displaying or managing Ultra's `unique` sessions.
- Changing the startup `clear_schedule` behaviour or its warning.
- Any change to `octopus` mode handling.
- Stopping other providers' modes beyond Axle; unknown provider types keep today's behaviour.

## Further Notes

- The charger itself rejects `schedule.set` while an energy provider is active, so the app was not
  overwriting Ultra's sessions; the harm was log noise, futile push attempts and lock control.
- The raw payload was captured with a read-only script against the live charger; it only read the
  `applied` object.
