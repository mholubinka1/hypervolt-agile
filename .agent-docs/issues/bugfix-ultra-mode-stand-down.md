# Issues: bugfix-ultra-mode-stand-down

## Detect Ultra from schedules.get and stop the parse warning

**Blocked by**: None

**User stories**: 1, 2, 5, 8

### What to build

When the charger's `schedules.get` response reports an applied schedule of type `axle`, record the
activation mode as `ultra` without parsing the provider's `unique` sessions, and emit no warning.
Log one INFO line when Ultra becomes active and one when the charger returns to a normal mode.
Recurring and octopus schedules, and the warning for a genuinely malformed recurring session, are
unchanged. Add `ultra` to the activation modes.

### Acceptance criteria

- [ ] Given an Axle `schedules.get` payload with `unique` sessions, when it is handled, then the
      emitted state has activation mode `ultra` and no sessions, and no warning is logged
- [ ] Given an Axle payload with an empty session list, when it is handled, then activation mode
      is still `ultra`
- [ ] Given the previous poll was normal and the next is Axle, then exactly one INFO line says Ultra
      is active, and further Axle polls log no additional line
- [ ] Given the previous poll was Axle and the next is a normal recurring payload, then exactly one
      INFO line says Ultra has ended and the normal activation mode is restored
- [ ] Given a malformed recurring payload, when it is handled, then the existing parse warning is
      still logged

---

## Coordinator stands down while Ultra is active

**Blocked by**: #196

**User stories**: 3, 4, 6, 7

### What to build

While the charger's activation mode is `ultra`, the coordinator neither pushes an Agile schedule nor
locks/unlocks the charger. When the mode returns to normal, pushing and lock control resume without
a restart. LED themes and charger-state refresh keep running throughout.

### Acceptance criteria

- [ ] Given the car is plugged in and activation mode is `ultra`, when the coordinator runs, then no
      schedule is applied and no lock or unlock is issued
- [ ] Given activation mode changes from `ultra` back to `schedule`, when the coordinator next runs,
      then schedule application and lock control resume
- [ ] Given activation mode is `ultra`, when the coordinator runs, then charger state refresh and LED
      state application still happen

---
