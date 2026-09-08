# Issues: feature-led-theme-activation-logging

## Resolved LED theme carries an `active_until` — [#149](https://github.com/mholubinka1/hypervolt-agile/issues/149)

**Blocked by**: None

**User stories**: 7 (and enables 2, 6)

### What to build

Add `active_until: datetime | None = None` to `LedTheme` — the resolution result, meaning
"the instant this theme is expected to stop applying", `None` when unknown. `resolve_theme`
populates it on the fresh defensive copy it already returns: for a calendar (custom or
built-in) match, the end of the matched year-anchored window (`_resolve_from` hands the end
back alongside the theme); for an extension match on either the primary or the fallback
pass, whatever the provider set on the `LedTheme` it returned. The Saints FC extension's
`resolve()` sets `active_until` to the latest `kick-off + match window` across the fixtures
whose window currently contains `now`; its `resolve_fallback()` leaves it `None`. The field
is never read for display or wire state. ADR 0020 records the decision.

### Acceptance criteria

- [ ] Given a custom or built-in theme matches, when `resolve_theme` returns, then the
      returned theme's `active_until` is the matched window's end datetime
- [ ] Given the Saints extension's `resolve()` returns a strip inside a match window, when
      it is resolved, then `active_until` is that fixture's kick-off + 3h
- [ ] Given two Southampton fixtures on one local date whose windows both contain `now`,
      when `resolve()` is resolved, then `active_until` is the later of the two window ends
- [ ] Given the Saints extension's `resolve_fallback()` returns the rest-of-day strip, when
      it is resolved, then `active_until` is `None`
- [ ] Given an extension whose returned theme sets no `active_until`, when `resolve_theme`
      returns it, then `active_until` is `None`
- [ ] `LedTheme()` still constructs with no `active_until` argument, defaulting to `None`;
      `DEFAULT_BUILT_IN_THEMES` entries and the `(LedTheme, Window, Window)` tuples are
      unaffected
- [ ] The existing `resolve_theme` equality assertions in
      `tests/hypervolt/test_led_resolve_custom_themes.py` are updated to account for the
      populated `active_until` on the returned copy (field assertions or
      `dataclasses.replace`)
- [ ] `resolve_theme`'s defensive copy still deep-copies `leds` and carries `always_on`
      through unchanged

---

## Coordinator logs LED theme activation, deactivation and swaps — [#150](https://github.com/mholubinka1/hypervolt-agile/issues/150)

**Blocked by**: [#149](https://github.com/mholubinka1/hypervolt-agile/issues/149)

**User stories**: 1, 2, 3, 4, 5, 6

### What to build

`ScheduleCoordinator` tracks the theme currently displayed on the ring
(`_active_theme_name`, `_active_theme_since`, both starting empty, no cross-restart
persistence) and logs one INFO line whenever it changes. A helper is called on both
outcomes of `_apply_led_state` — with the resolved theme's `effect_name` on the branch that
applies brightness 1.0, and with `None` on the branch that clears to 0.0 — sharing the
single `datetime.now(TIMEZONE)` already taken for `resolve_theme`. Identity is `effect_name`
alone. Transitions:

- none → `A`: `LED theme 'A' active` plus, when `active_until` is known,
  ` until <YYYY-MM-DD HH:MM local> (~<duration>)`
- `A` → none: `LED theme 'A' cleared after <duration>`
- `A` → `B`: one line — `LED theme 'B' active[ until …] — replaced 'A' after <duration>` —
  and no separate clear line
- `A` → `A`, none → none: nothing

`_apply_led_state`'s early returns (LED config absent/disabled, `is_charging` still `None`)
never reach the helper, so tracked state is untouched on those cycles. Add
`format_duration(td: timedelta) -> str` to `common/utils.py` — `2h58m` / `47m` / `38s`,
exact hour `2h00m`, zero or negative `0s` — used for both the measured lit duration and the
`(~…)` time-to-go. The predicted-end timestamp is rendered in charger-local time via
`.astimezone(TIMEZONE)`.

### Acceptance criteria

- [ ] Given no theme is displayed, when a theme with a known `active_until` first lights the
      ring, then one INFO line is logged naming the effect, the local predicted-end
      timestamp, and the approximate time-to-go
- [ ] Given the resolved theme's `active_until` is `None`, when it first lights the ring,
      then the line is `LED theme '<name>' active` with no `until` clause and no `(~…)`
- [ ] Given a theme is displayed, when the ring goes dark on a later cycle and no theme
      replaces it, then one INFO line is logged naming the effect and the measured elapsed
      time it was lit
- [ ] Given theme A is displayed, when theme B lights the ring on the same cycle A leaves,
      then exactly one INFO line is logged — naming B, B's predicted end if known, and
      `replaced 'A' after <duration>` — and no separate "cleared" line for A
- [ ] Given a theme stays displayed unchanged, when many poll cycles run, then exactly one
      INFO line was logged for that theme across all of them
- [ ] Given a freshly constructed coordinator (post-restart) whose first cycle displays a
      theme, when that cycle runs, then an activation line is logged
- [ ] Given `_apply_led_state` returns early because `is_charging` is `None` or LED config
      is disabled/absent, when the cycle runs, then no theme-transition line is logged and
      the tracked active theme is unchanged
- [ ] `format_duration` renders `2h58m`, `47m`, `38s`, an exact hour as `2h00m`, and a zero
      or negative delta as `0s`
- [ ] The `Setting LED brightness` / `Setting LED effect` lines in `charger.py` and what
      the ring displays are unchanged

---
