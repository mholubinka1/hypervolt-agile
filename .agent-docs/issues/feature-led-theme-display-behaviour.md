> Work complete — PR ready to merge.

# Issues: feature-led-theme-display-behaviour

## 1. Binary brightness and the always_on display gate

**Blocked by**: None

**GitHub**: #114

**User stories**: 1, 2, 3, 4, 5

### What to build

Replace dimmable brightness and the "plain white while charging" state with a binary rule,
and give custom/built-in themes a per-theme display gate.

- Remove the `brightness` field from `LedConfig` (keep `enabled`) and set it to forbid
  unknown keys, so a leftover `brightness` — or any typo under `led:` — raises at config
  load instead of being silently ignored.
- `LedTheme` (frozen dataclass) gains `always_on: bool` defaulting to `False`.
  `resolve_theme`'s defensive copy carries `always_on` through alongside `effect_name` and
  the deep-copied `leds`.
- The base of `CustomLedTheme` / `BuiltInLedTheme` gains an `always_on: bool` field
  defaulting to `False` — an ordinary fail-fast pydantic field — and also forbids unknown
  keys (a misspelled `always_on` / `start` / `end` raises). The custom- and built-in-theme
  loaders pass the configured `always_on` into the `LedTheme` they construct.
- `ScheduleCoordinator._apply_led_state` collapses to: if `led` is absent/disabled or
  `is_charging` is unknown, do nothing; resolve the theme; if a theme resolved and
  (`theme.always_on` or the car is charging), push it at brightness `1.0`; otherwise push
  the off state (`0.0`, no effect). `car_plugged` and any brightness config are no longer
  read anywhere in this method.
- Raises **ADR 0014** — binary LED brightness; LED display decoupled from plug state;
  `led.brightness` removed; `always_on` flag (default `false`); always-on themes light an
  unplugged charger. Supersedes ADR 0010.

### Acceptance criteria

- [x] Given a resolved theme with `always_on=True`, when the car is not charging (and not
      plugged in), then the LEDs are set to that theme at full brightness.
- [x] Given a resolved theme with `always_on=False`, when the car is not charging, then the
      LEDs are set to the off state.
- [x] Given a resolved theme with `always_on=False`, when the car is charging, then the LEDs
      are set to that theme at full brightness.
- [x] Given no theme resolves, when the car is charging, then the LEDs are set to the off
      state (no plain-white state).
- [x] Given `is_charging` is unknown, `_apply_led_state` makes no call to the charger.
- [x] `CustomLedTheme` and `BuiltInLedTheme` default `always_on` to `False` when omitted; a
      non-bool value raises at config load.
- [x] A `config.yml` with `led.brightness` set raises at load; an unknown key under `led:`
      raises at load.
- [x] The custom- and built-in-theme loaders produce `LedTheme`s carrying the configured
      `always_on`; `resolve_theme` returns it unchanged through the defensive copy.
- [x] ADR 0014 committed under `.agent-docs/adr/`, and ADR 0010 marked superseded.

---

## 2. resolve_theme fallback pass

**Blocked by**: #114

**GitHub**: #115

**User stories**: 9, 10

### What to build

Add a second resolution pass so an extension can be consulted *below* custom and built-in
themes as well as above them.

- `LedThemeProvider` gains an **optional** `resolve_fallback(now) -> LedTheme | None`
  coroutine hook, declared the same way as `start` / `stop` — documented in a comment, not a
  Protocol member — and reached via `hasattr`.
- `ExtensionWrapper` gains a `resolve_fallback` method mirroring its `resolve`: the same
  exception isolation, the same de-duplication of repeated warning lines, the same
  "raise if the return is neither `None` nor an `LedTheme`" guard, and the same recovery
  info log. An extension that does not implement the hook yields `None`.
- `resolve_theme` runs the primary walk unchanged (extensions in list order → custom themes
  → built-in themes). If the primary walk produced nothing, it then walks each extension's
  `resolve_fallback` in list order and takes the first non-`None`. Whichever pass produced
  the match then goes through the existing defensive copy.
- The primary walk order is unchanged and out of scope.
- Raises **ADR 0015** — the `resolve_fallback` hook and the second pass; the mechanism that
  lets a single extension rank both above and below the theme tiers depending on time.

### Acceptance criteria

- [x] Given the primary walk finds a theme, `resolve_theme` returns it (defensively copied)
      and no `resolve_fallback` is consulted.
- [x] Given the primary walk finds nothing and one extension's `resolve_fallback` returns a
      theme, `resolve_theme` returns that theme (defensively copied, `always_on` preserved).
- [x] Given two extensions both return from `resolve_fallback`, the earlier one in config
      list order wins.
- [x] Given an extension's `resolve_fallback` raises, it is isolated (logged once, treated
      as `None`) and the next extension's `resolve_fallback` is still consulted.
- [x] Given an extension without a `resolve_fallback` method, `resolve_theme` handles it as
      yielding `None`.
- [x] ADR 0015 committed.

---

## 3. Saints strip: real colours, auto kick-off discovery, hourly polling

**Blocked by**: #114

**GitHub**: #116

**User stories**: 8, 13, 14

### What to build

Move the Saints extension off its placeholder pattern and daily single-shot poll, and start
recording kick-off times — without yet narrowing the display to the match window (that is
issue #4).

- Load the tuned colour map from the repo `themes/` directory once at construction, via
  `hypervolt.led`'s public helpers. A missing/malformed file raises during construction, so
  the extension is logged and treated as absent (ADR 0007). Delete the placeholder
  red/white LED builder and its colour constants.
- Replace the fixture-date `set` with a mapping of local match date → list of
  timezone-aware kick-off instants. An empty list means "match that day, kick-off unknown".
- Parse each fixture's kick-off: the UTC timestamp field preferred; else the local-time +
  local-date fields combined and attached to the charger-local timezone; else record the
  date with an empty list.
- Replace the once-daily poll with an interval poll driven by a new `poll_interval_hours`
  config field (default `1`, must be a positive number); remove the old `poll_time` field
  and its parser. Each poll records **today and tomorrow** and prunes entries for dates now
  in the past (fixes unbounded growth). `start()` keeps its bootstrap check for today.
- `resolve(now)` returns the real-colour strip for the **whole local match date** as a
  **charging-gated** theme (`always_on=False`, relying on issue #1's field default), with
  wire effect name `saints_fc`. Kick-off times are recorded but not yet consulted by
  `resolve`.
- Raises **ADR 0016** — a shipped extension may read a repo theme asset via `hypervolt.led`'s
  public API; narrows ADR 0006's "self-contained" to "no shared mutable state / no
  cross-extension coupling".

### Acceptance criteria

- [x] On a match date, `resolve` returns a theme whose LEDs equal the parsed
      `themes/saints_fc.yaml` colour map and whose wire effect name is `saints_fc`, with
      `always_on=False`.
- [x] On a non-match date, `resolve` returns `None`.
- [x] Given the colour file is missing, construction raises and the extension is absent from
      the priority stack (logged; the app still starts).
- [x] The fixture store maps each recorded local date to a list of timezone-aware kick-off
      instants; a fixture with an unparseable/absent kick-off is recorded with an empty list.
- [x] Kick-off parsing handles the UTC timestamp field and the local-time+local-date pair,
      and falls back to the empty list on garbage.
- [x] The extension polls on the configured interval (default hourly), recording today and
      tomorrow each poll, and drops entries for past dates.
- [x] The old `poll_time` field is gone; a `poll_interval_hours` that is not a positive
      number raises at extension load.
- [x] The placeholder LED builder and its `_RED` / `_WHITE` constants no longer exist.
- [x] ADR 0016 committed.

---

## 4. Saints match window and split priority

**Blocked by**: #115, #116

**User stories**: 6, 7, 9, 10, 11, 12

**GitHub**: #117

### What to build

Narrow the Saints display to the match itself and give it its two-mode priority.

- Define the match window as `kick-off − 30 minutes … kick-off + 3 hours`, as fixed module
  constants. The whole span behaves identically — there is no weaker lead-in sub-window.
- Factor the "is `now` inside any of today's match windows?" check into a helper shared by
  `resolve` and `resolve_fallback`.
- `resolve(now)`: on a match date, if `now` is inside any of that date's windows (union
  across a double-header), return the strip with `always_on=True`; otherwise `None`. A
  match date whose kick-off list is empty yields no window, so `resolve` returns `None` all
  day.
- `resolve_fallback(now)`: on a match date, if `now` is *not* inside any window (including
  the empty-kick-off-list case), return the strip with `always_on=False`; otherwise `None`.
- No ADR (covered by 0015 + the spec's window rule).

### Acceptance criteria

- [x] Given a fixture with a known kick-off, `resolve` returns the strip (`always_on=True`)
      when `now` is within `[kickoff − 30m, kickoff + 3h]`, and `None` one second before
      `kickoff − 30m` and one second after `kickoff + 3h`.
- [x] Given two fixtures on one local date, `resolve` returns the strip during either
      fixture's window (union).
- [x] Given a fixture date whose kick-off is unknown (empty list), `resolve` returns `None`
      for every `now` that day and `resolve_fallback` returns the strip (`always_on=False`).
- [x] Given a match date and `now` outside every window, `resolve` returns `None` and
      `resolve_fallback` returns the strip with `always_on=False`.
- [x] Given a non-match date, both `resolve` and `resolve_fallback` return `None`.
- [x] The window bounds are correct across a DST transition (kick-offs compared as
      timezone-aware instants).
- [x] End-to-end via the coordinator, on a fixture date with an `always_on:true` custom
      theme configured: the custom theme shows before `kickoff − 30m` and after
      `kickoff + 3h`; the Saints strip shows during the window. With no custom theme
      configured: the charger is off before the window when idle, and shows the Saints strip
      only while charging.

---
