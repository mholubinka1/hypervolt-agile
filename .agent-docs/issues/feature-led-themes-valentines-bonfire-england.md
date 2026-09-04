# Issues: feature-led-themes-valentines-bonfire-england

## 1. Reorganise extensions/ into provider-kind subfolders

**Blocked by**: None

**GitHub**: #131

**User stories**: 8

### What to build

Move the existing LED theme extension into a new `extensions/themes/` subfolder, and
establish (without populating) `extensions/vehicles/` as its sibling, so `extensions/` is
organised by the two provider kinds ADR 0017 decided the extension loader will distinguish
once it becomes multi-protocol (not yet implemented). Pure reorganisation — no loader/
mechanism code changes, no behaviour change.

- Move `extensions/saints_fc.py` → `extensions/themes/saints_fc.py`.
- Move `tests/extensions/test_saints_fc.py` → `tests/extensions/themes/test_saints_fc.py`.
- `config/config.yml.template`'s `saints_fc` extension example entry renames from
  `name: saints_fc` to `name: themes/saints_fc`.
- `README.md`'s extensions section updated to reference the new path.
- Append an amendment to `.agent-docs/adr/0017-extension-loader-generalised-across-provider-kinds.md`
  recording the two subfolders and the `extensions/vehicles/` path for the Volvo feature
  (in this repo's established pattern of amending ADRs in place, not rewriting them).
- Fix the two `extensions/volvo.py` path references in
  `.agent-docs/specs/feature-volvo-battery-level.md` (currently lines 153 and 248) to
  `extensions/vehicles/volvo.py` — a path-reference correction only, no other change to that
  spec or its issues (#124–#129).
- Raises **ADR** — two provider-kind-named subfolders under one `extensions/` directory;
  `--extensions-dir` keeps pointing at the one root; a hard, documented break for the one
  already-shipped extension (`saints_fc` → `themes/saints_fc`).

### Acceptance criteria

- [ ] `extensions/saints_fc.py` no longer exists; `extensions/themes/saints_fc.py` contains
      the identical module, byte-for-byte behaviourally unchanged.
- [ ] `tests/extensions/themes/test_saints_fc.py` exists and passes unmodified in substance
      (only its own path and any relative imports changed).
- [ ] Given a `led.extensions` entry `name: themes/saints_fc`, `load_extensions` resolves and
      loads `extensions/themes/saints_fc.py` with no code change to `load_extensions` /
      `_load_provider_class`.
- [ ] `config/config.yml.template` and `README.md` reference `themes/saints_fc`, not
      `saints_fc`.
- [ ] `extensions/vehicles/` is not created or populated by this change.
- [ ] `.agent-docs/specs/feature-volvo-battery-level.md`'s two `extensions/volvo.py`
      references now read `extensions/vehicles/volvo.py`; nothing else in that file changes.
- [ ] ADR committed under `.agent-docs/adr/`; ADR 0017 carries the amendment.
- [ ] Full test suite passes with no other behavioural change.

---

## 2. Valentine's Day custom theme

**Blocked by**: None

**GitHub**: #132

**User stories**: 1

### What to build

Ship a Valentine's Day custom theme using the existing, unmodified custom-theme mechanism —
no new code paths, purely a new data file plus its config/doc surface.

- `themes/valentines.yaml`: `default_colour` + `segments`, identical shape to
  `themes/saints_fc.yaml`. Start with a reasonable placeholder, then take it through the
  iterative on-charger design workflow (design against
  `themes/reference/charger_led_map.html`, push live with `scripts/show_led_theme.py`,
  repeat) until it looks right on the real hardware — `scripts/show_led_theme.py` already
  supports this unmodified via its existing `--effect valentines` flag.
- `config/config.yml.template` gains a commented Valentine's Day `custom_themes` example
  entry: `effect: valentines`, `start: "02-14"`, `end: "02-15"`.
- A shipped-theme test alongside the existing `tests/themes/test_shipped_themes.py` pattern.

### Acceptance criteria

- [ ] `themes/valentines.yaml` exists and `hypervolt.led.load_custom_effect` parses it to 51
      LEDs with no error.
- [ ] Given a `custom_themes` entry `effect: valentines, start: "02-14", end: "02-15"`, the
      app resolves and loads `themes/valentines.yaml` via the existing, unmodified
      `load_custom_themes` path.
- [ ] The colours have been pushed to the real charger via
      `scripts/show_led_theme.py --effect valentines` and iterated against
      `themes/reference/charger_led_map.html` until confirmed to look right on the hardware.
- [ ] `config/config.yml.template` documents the Valentine's Day example entry.
- [ ] A shipped-theme test asserts `themes/valentines.yaml` loads to 51 LEDs.
- [ ] No changes to `app/hypervolt/led.py`, `app/config.py`, or `app/schedule/coordinator.py`.

---

## 3. Extension priority field

**Blocked by**: None

**GitHub**: #133

**User stories**: 6

### What to build

Replace implicit config-list-order with an explicit, optional per-extension `priority` that
`load_extensions` sorts by, so a future config can guarantee one extension always outranks
another regardless of list position — tested generically via stub extensions, independent of
any specific extension using it yet.

- `ExtensionEntry` (`app/config.py`) gains `priority: int = 0` (higher wins).
- `load_extensions` (`app/hypervolt/led.py`) sorts its returned `list[ExtensionWrapper]` by
  `(-priority, original_list_index)` — a **stable** sort, so entries left at the default `0`
  keep today's exact list-order behaviour.
- `resolve_theme` is **not** modified — both its primary and fallback walks already iterate
  `extensions` in whatever order they're handed, so the sort in `load_extensions` covers both
  passes for free.
- Raises **ADR** — explicit per-extension `priority` (default 0, higher wins, stable-sorted,
  applies uniformly to both `resolve_theme` passes) replaces implicit config-list-order as
  the tiebreak semantics. Alternative rejected: hardcoding one named extension above another
  inside `resolve_theme` — bakes team-specific logic into core resolution, which the
  framework has deliberately avoided everywhere else.

### Acceptance criteria

- [ ] `ExtensionEntry` defaults `priority` to `0` when omitted; accepts an explicit `int`;
      rejects a non-`int` (including `bool`) at config load.
- [ ] Given two extension entries both at the default priority, `load_extensions` preserves
      their original config list order (a stable-sort regression guard).
- [ ] Given two extension entries with explicit, differing priorities, `load_extensions`
      returns the higher-priority one first, regardless of their config list order.
- [ ] Given three or more extensions with mixed explicit/default priorities, the returned
      order is fully deterministic: descending priority, then original list order as tiebreak.
- [ ] `resolve_theme`'s primary walk consults the reordered list (a stub extension with
      higher priority wins the primary pass over a stub that would otherwise win by being
      listed first).
- [ ] `resolve_theme`'s fallback walk consults the same reordered list consistently (a stub
      extension with higher priority wins the fallback pass under the same conditions).
- [ ] `resolve_theme` itself has no code changes — verified by the diff, not just behaviour.
- [ ] ADR committed.

---

## 4. Animated theme mechanism: LedTheme.animated + coordinator animation task

**Blocked by**: None

**GitHub**: #134

**User stories**: 2, 3, 4

### What to build

Give the framework a general way for a resolved theme to declare itself animated, and give
`ScheduleCoordinator` a self-starting/self-stopping fast task that keeps re-pushing such a
theme's freshly-resolved LEDs — tested against a stub animated `LedThemeProvider`, with no
dependency on the Bonfire Night extension existing yet.

- `LedTheme` (frozen dataclass) gains `animated: bool = False`. `resolve_theme`'s defensive
  copy carries it through, alongside `always_on`.
- `ScheduleCoordinator` gains an `asyncio.Lock` guarding every charger-client-mutating call.
- On each normal-cadence `_apply_led_state` call (still driven by the unchanged
  `poll_every_secs`):
  - If the resolved theme has `animated=True` and no animation task is running, start one:
    an `asyncio.Task` looping `resolve_theme` + `apply_led_state` (both under the lock),
    sleeping `random.uniform(0.1, 0.2)` seconds between iterations — redrawn every cycle.
  - If the resolved theme is not animated (or nothing resolves) and a task *is* running,
    cancel and await it, then fall through to this cycle's normal single
    `apply_led_state` call.
- `main.py`'s existing `every(_poll, run, ...)` structure is unchanged — no new top-level
  loop, no new CLI flag, no config field.
- Raises **ADR** — the `animated` flag plus a coordinator-owned, self-starting/stopping
  background task at a jittered 100–200 ms cadence, lock-guarded against the normal cadence.
  Alternative rejected: a permanent second fast loop in `main.py` running year-round.

### Acceptance criteria

- [ ] `LedTheme.animated` defaults to `False`; `resolve_theme`'s defensive copy preserves it
      through both the primary-pass and fallback-pass return paths.
- [ ] Given a stub extension whose `resolve()` returns `animated=True` themes, the
      coordinator starts a background task that calls `apply_led_state` more than once within
      a short window shorter than `poll_every_secs` would alone produce.
- [ ] Given the same stub later returns `animated=False` (or `None`), the coordinator cancels
      and awaits the running animation task before its next normal `apply_led_state` call —
      no orphaned task, no further animation-task pushes after cancellation.
- [ ] The animation task's sleep interval varies between calls (not a fixed constant) —
      verified by sampling several consecutive intervals and asserting they are not all equal
      and each falls within `[0.1, 0.2]` seconds.
- [ ] A normal-cadence charger-client call and an in-flight animation-task charger-client call
      never interleave — verified by a test that provokes both near-simultaneously and asserts
      no partial/interleaved call is observed (the lock actually serialises them).
- [ ] Given no animated theme is ever resolved across a whole test run, behaviour and the
      number of `apply_led_state` calls are identical to before this change (regression guard
      for the 364-day case).
- [ ] `main.py` has no diff — confirmed by the PR diff, not just behaviour.
- [ ] ADR committed.

---

## 5. Bonfire Night extension

**Blocked by**: #131, #134

**GitHub**: #135

**User stories**: 2, 4

### What to build

The real animated theme: an extension that lights up Nov 5 with a freshly-randomised
orange/red/yellow flicker on every call, using issue #134's `animated` flag and issue #131's
`extensions/themes/` location.

- New `extensions/themes/bonfire.py`. `resolve(now)`: if `now` falls in the Nov 5 window
  (`start "11-05" 00:00`, `end "11-06" 00:00`, via the existing `parse_window_date` /
  `window_for_year` machinery), return a freshly-resampled `LedTheme` — all 51 LEDs drawn
  from an orange/red/yellow palette, `animated=True`; outside the window, `None`.
- No `resolve_fallback` — bonfire is active-or-not on its one date, the same shape as a
  custom theme's window; it naturally outranks custom/built-in themes via the existing
  primary-walk order while active, an accepted consequence rather than a new rule.
- The extension's own config gains an `always_on: bool` field (default `false`), set onto
  the `LedTheme` it returns, mirroring the custom/built-in theme knob.
- `config/config.yml.template` gains a commented `bonfire` extension example entry.
- `scripts/show_led_theme.py` is extended to support pushing a **non-static, extension-based**
  theme, not just a `themes/*.yaml` file: instead of loading one YAML once and holding it, it
  repeatedly calls the extension's `resolve(now)` (or a direct frame-generation entry point)
  and re-pushes on an interval, so the flicker itself can be watched and tuned live on the
  real charger — not just a single static frame. This is the tool the palette gets tuned
  through (see the last acceptance criterion below); it is retained as part of this work, not
  discarded afterwards.

### Acceptance criteria

- [ ] Given `now` inside the Nov 5 window, `resolve(now)` returns a `LedTheme` with 51 LEDs,
      `animated=True`, every colour drawn from the orange/red/yellow palette. Sampled across
      many consecutive calls, every colour returned stays within that palette (no stray
      values) — not just true for one sample.
- [ ] `resolve(now)` returns `None` at the exact boundary instants — one second before the
      window opens and one second after it closes — not just "somewhere outside," proving the
      window is closed at both ends.
- [ ] Two consecutive `resolve()` calls while inside the window return LEDs that differ from
      each other (proves real per-call randomisation, not one fixed frame).
- [ ] `resolve_fallback` is either absent or always returns `None` — bonfire never competes
      for the fallback slot.
- [ ] `scripts/show_led_theme.py` can push bonfire's live, repeatedly-regenerated frames to
      the real charger (not just a single static push) — used to iterate the palette until it
      reads as fire on the real hardware, the same iterative loop the other two themes go
      through against their static YAML files.
- [ ] Given the extension's `always_on` config is `true`, the returned `LedTheme.always_on`
      is `True`; given `false`/omitted, it is `False` (default).
- [ ] End-to-end via the coordinator: on Nov 5, with bonfire configured, the animation task
      from issue #134 starts and pushes differing frames; outside Nov 5, it never starts.
- [ ] `config/config.yml.template` documents the `bonfire` extension example entry.

---

## 6. England match-day extension

**Blocked by**: #131, #133

**GitHub**: #136

**User stories**: 5, 6, 7

### What to build

A second match-day extension for the England national team, built the same way as
`extensions/themes/saints_fc.py` — duplicated, not extracted into a shared base — and wired
to always outrank Saints via issue #133's priority field.

- New `extensions/themes/england.py`, copy-pasted from `extensions/themes/saints_fc.py`'s
  pattern: `_matches: dict[date, list[datetime]]` fixture store; kick-off parsing
  (`strTimestamp` preferred, else `strTimeLocal`+`dateEventLocal`, else empty list);
  `poll_interval_hours` config (default 1, positive and finite); `_poll_once` records
  today+tomorrow and prunes past dates; `_MATCH_LEADIN = 30 min` / `_MATCH_WINDOW = 3 h`;
  `resolve(now)` → `always_on=True` strip inside any window (union across a double-header)
  else `None`; `resolve_fallback(now)` → `always_on=False` strip on a match date outside
  every window (including unknown-kick-off dates) else `None`. Wire `effect_name = "england"`.
- England's own TheSportsDB `team_id` — research the correct ID during this issue (not
  Southampton's `134778`).
- `themes/england.yaml`: same shape as `themes/saints_fc.yaml`. Start with a reasonable
  placeholder, then take it through the iterative on-charger design workflow (design against
  `themes/reference/charger_led_map.html`, push live with
  `scripts/show_led_theme.py --effect england`, repeat) until it looks right on the real
  hardware.
- `config/config.yml.template`'s `england` extension example entry is given a higher
  `priority` than the `saints_fc` example (e.g. `priority: 1` vs Saints' default/unset `0`),
  so a deployment following the shipped example gets England-over-Saints by default.

### Acceptance criteria

- [ ] `themes/england.yaml` exists and `hypervolt.led.load_custom_effect` parses it to 51
      LEDs with no error. A `tests/themes/test_shipped_themes.py`-style shipped-theme test
      asserts this, mirroring the pattern used for `themes/valentines.yaml` in #132.
- [ ] The colours have been pushed to the real charger via
      `scripts/show_led_theme.py --effect england` and iterated against
      `themes/reference/charger_led_map.html` until confirmed to look right on the hardware.
- [ ] Given an England fixture with a known kick-off, `resolve` returns the strip
      (`always_on=True`, `effect_name="england"`) when `now` is within
      `[kickoff − 30m, kickoff + 3h]`, and `None` outside it.
- [ ] Kick-off parsing is tested for both source shapes: a fixture whose event carries
      `strTimestamp` (UTC) parses correctly from it, and a fixture without `strTimestamp` but
      with `strTimeLocal`+`dateEventLocal` parses correctly from that fallback pair — mirroring
      `tests/extensions/themes/test_saints_fc.py`'s existing coverage of both branches.
- [ ] Given an England fixture date with the kick-off unknown, `resolve` returns `None` all
      day and `resolve_fallback` returns the strip (`always_on=False`).
- [ ] Given a double-header (two England fixtures one date), `resolve` covers either
      fixture's window.
- [ ] Given a non-fixture date, both `resolve` and `resolve_fallback` return `None`.
- [ ] The match window's bounds are correct across a DST transition — a fixture whose kick-off
      falls near a clock change resolves the same real-world window either side of it (mirrors
      `tests/extensions/themes/test_saints_fc.py`'s existing DST-transition case).
- [ ] `poll_interval_hours` validation matches Saints' (positive, finite, default 1).
- [ ] End-to-end via the coordinator, with both `england` (priority 1) and `saints_fc`
      (priority 0, or unset) configured and both extensions' match windows simultaneously
      active: England's strip is the one applied, regardless of which extension is listed
      first in `config.yml`.
- [ ] `config/config.yml.template` documents the `england` example entry with its higher
      `priority` relative to `saints_fc`.

---
