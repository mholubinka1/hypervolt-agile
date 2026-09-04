# LED Themes: Valentine's Day, Bonfire Night, and England

## Problem Statement

Southampton FC has a match-day strip and the theming mechanism behind it (binary brightness,
the `always_on` display gate, the `themes/` colour-map directory, the priority stack) is
proven. Three gaps remain:

1. There is only one seasonal custom theme. The operator wants a Valentine's Day strip
   following the same pattern.
2. Every existing theme is a fixed colour map — the same 51 colours every time it displays.
   The operator wants a Bonfire Night theme that actually flickers like a fire, which today's
   theme mechanism cannot express: custom and built-in themes are loaded once into a static
   `LedTheme` at startup, so nothing currently re-renders per display cycle.
3. Southampton FC is one club; the operator also wants an England theme for international
   matches, built the same way as the Saints strip — but England must always take priority
   over Southampton when both could apply, and today's extension ordering is just config list
   order, which is fragile and not what the operator wants to rely on.

## Solution

Three new LED themes, each landing through the mechanism that actually fits it:

- **Valentine's Day** — a plain static `themes/valentines.yaml` custom theme, exactly the
  shape of `themes/saints_fc.yaml`. Active Feb 14 only. Colours designed later against
  `themes/reference/charger_led_map.html`, the same process the Saints strip went through.
- **Bonfire Night** — a new LED Theme Extension, `extensions/themes/bonfire.py`. Active Nov 5
  only. Because only an extension's `resolve(now)` is called fresh on every display cycle,
  animation is only possible through the extension mechanism — so `LedTheme` gains an
  `animated` flag, and `ScheduleCoordinator` gains a self-starting/self-stopping background
  task that pushes freshly-randomised orange/red/yellow frames at a jittered 100–200 ms
  cadence for as long as an animated theme is resolved, and does nothing extra the other 364
  days of the year.
- **England** — a second match-day extension, `extensions/themes/england.py`, built the same
  way as `extensions/themes/saints_fc.py` (TheSportsDB fixture polling, the match-window
  `resolve`/`resolve_fallback` split from ADR 0015) but for the England national team, and
  given an explicit, higher `priority` than Saints so it always wins when both could show —
  a new per-extension `priority` field replaces implicit config-list-order as the tiebreak.

Alongside this, `extensions/` gains two subfolders — `extensions/themes/` for every
`LedThemeProvider` (Saints, England, Bonfire) and `extensions/vehicles/` reserved for the
separate, already-planned Volvo `VehicleProvider` work (ADR 0017) — so the one shared
extension loader's two provider kinds are visible in the directory layout, at the cost of a
documented hard rename for the one already-shipped extension.

## User Stories

1. As an operator, I want a Valentine's Day custom theme shipped the same way Saints FC is,
   so I can enable it in `config.yml` like any other seasonal theme.
2. As an operator, I want the Bonfire Night theme to visibly flicker — different LEDs, sampled
   from a fire-like orange/red/yellow palette, on every refresh — rather than a fixed pattern,
   so it actually reads as a bonfire rather than a static autumn colour scheme.
3. As an operator, I want that flicker to only cost anything (extra charger writes, a faster
   refresh loop) while Bonfire Night is actually active, so the other 364 days a year the app
   behaves exactly as it does today.
4. As an operator, I want the bonfire flicker to still honour `always_on` like every other
   theme — lit on an idle unplugged charger if I set it, charging-gated otherwise — so I don't
   need to learn a second display-gate concept for the one animated theme.
5. As an operator, I want an England theme that lights up for England internationals the same
   way Saints FC lights up for Southampton matches — kick-off window, charging-gated fallback
   the rest of a match day — so international duty gets the same treatment as club football.
6. As an operator, I want England to always win if an England match and a Southampton match
   were ever both live at once, regardless of the order I list the two extensions in
   `config.yml`, so I don't have to remember a fragile ordering rule to get the outcome I want.
7. As a maintainer, I want the England extension built the same way as Saints — including
   accepting the duplication rather than extracting a shared base — so each file stays fully
   readable on its own, at the cost of two files to update if the shared shape ever changes.
8. As a maintainer, I want `extensions/` organised by provider kind (`themes/`, `vehicles/`)
   so the extension loader — decided by ADR 0017 to become multi-protocol once the Volvo
   feature lands, though that implementation hasn't happened yet — gets a directory layout
   that already reflects what it will load, without needing a second loader or a second CLI
   flag later.

## Implementation Decisions

### Valentine's Day — no new mechanism

- `themes/valentines.yaml`, identical shape to `themes/saints_fc.yaml`
  (`default_colour` + `segments`/`ranges`/`indices`), loaded by the existing
  `load_custom_effect`/`load_custom_themes` path — nothing here is new.
- Window: `start: "02-14"`, `end: "02-15"` (single day, the same `MM-DD` shape every other
  custom/built-in theme window already uses).
- `always_on` is the existing Phase 2 per-theme field (ADR 0014), default `false`; the
  operator sets it like any other theme. No new field, no new gate.
- Colours: start as a reasonable placeholder, then go through the iterative on-charger design
  workflow below (against `themes/reference/charger_led_map.html` and
  `scripts/show_led_theme.py`) — the same process Saints FC's stripe went through — until
  they're right. Exact final values are not decided in this spec.

### Bonfire Night — the animation mechanism

**Why it must be an extension, not a `themes/*.yaml` file:** `load_custom_themes` /
`load_built_in_themes` build a fixed list of `(LedTheme, Window, Window)` tuples once at
startup — the `LedTheme.leds` array is static, reused unchanged on every `resolve_theme`
call for that theme's whole active window. Only a `LedThemeProvider`'s `resolve(now)` is
invoked fresh every cycle, so it's the only seam that can return different LEDs each time.

- New extension `extensions/themes/bonfire.py`. `resolve(now)`: if `now` falls in the Nov 5
  window (`start "11-05" 00:00`, `end "11-06" 00:00` — reuse `parse_window_date` /
  `window_for_year`, the same date-window machinery every custom/built-in theme already uses,
  rather than hand-rolling new date comparison), return a freshly-randomised `LedTheme` with
  all 51 LEDs resampled from an orange/red/yellow palette; outside the window, `None`.
- No `resolve_fallback` — bonfire isn't competing for a below-other-themes fallback slot the
  way Saints is; it's simply active-or-not on its one date, the same shape as a custom
  theme's window. As an extension it does outrank custom/built-in themes on Nov 5 through the
  existing primary-walk order — an accepted consequence of the mechanism it's built on, not a
  new priority rule.
- Its own extension config gains an `always_on: bool` field (default `false`), mirroring the
  custom/built-in theme knob, set onto the `LedTheme` it constructs — from the operator's
  side, bonfire behaves like any other themed window even though it's implemented as an
  extension.

**The `animated` flag and the coordinator's animation task:**

- `LedTheme` (frozen dataclass) gains `animated: bool = False`. `resolve_theme`'s defensive
  copy carries it through, exactly as `always_on` already is. Bonfire sets `animated=True` on
  every `LedTheme` it returns; every other source (custom, built-in, Saints, England) leaves
  it at the default `False`.
- `ScheduleCoordinator` gains a self-managed background task — **not** a second permanent
  loop in `main.py`, and no change to `main.py`'s existing `every(_poll, run, ...)`
  structure. On each normal-cadence `_apply_led_state` call (still driven by the unchanged
  `poll_every_secs`):
  - If the resolved theme has `animated=True` and no animation task is currently running,
    start one: an `asyncio.Task` looping `resolve_theme` + `apply_led_state`, sleeping a
    freshly-drawn `random.uniform(0.1, 0.2)` seconds between iterations — redrawn every
    cycle, not a fixed interval, so the flicker itself reads as irregular rather than
    metronomic.
  - If the resolved theme is not animated (or nothing resolves) and a task *is* running,
    cancel and await it before falling through to this cycle's normal single
    `apply_led_state` call.
  - A single `asyncio.Lock`, owned by the coordinator, guards every charger-client-mutating
    call from both the normal cadence and the animation task, so the two can never interleave
    a partial charger operation.
- Net effect: for 364 days a year, cadence and behaviour are unchanged — one
  `apply_led_state` call per `poll_every_secs` cycle, no lock contention because nothing else
  ever takes the lock. The fast task exists only while Bonfire Night's window is active.
- Raises **an ADR**: the `animated` flag plus a coordinator-owned, self-starting/stopping
  background task at a jittered 100–200 ms cadence, lock-guarded against the normal cadence.
  Alternative rejected: a permanent second fast loop in `main.py` running year-round —
  rejected for needless charger/network load 364 days a year for a once-a-year effect.

### England — a second match-day extension

- New `extensions/themes/england.py`, built the same way as `extensions/themes/saints_fc.py`
  — **copy-pasted, not extracted into a shared base**, despite the two sharing roughly 90% of
  their logic. Explicit decision: duplication is accepted over a shared module, so each file
  stays fully readable in isolation, at the cost of two places to update if the shared shape
  changes later.
- Same shape as Saints: `_matches: dict[date, list[datetime]]` fixture store; kick-off
  parsing (`strTimestamp` UTC preferred, else `strTimeLocal`+`dateEventLocal`, else an empty
  list meaning kick-off unknown); `poll_interval_hours` config (default 1, positive and
  finite); `_poll_once` records today+tomorrow and prunes past dates; `_MATCH_LEADIN = 30 min`
  / `_MATCH_WINDOW = 3 h` fixed constants; `resolve(now)` → `always_on=True` strip inside any
  window (union across a double-header) else `None`; `resolve_fallback(now)` →
  `always_on=False` strip on a match date outside every window (including an
  unknown-kick-off date) else `None`.
- England's own TheSportsDB `team_id` (not Southampton's `134778`) is a research item during
  implementation, not a spec decision.
- Colours: `themes/england.yaml` (top-level `themes/`, unchanged location), loaded via
  `THEMES_DIR`/`load_custom_effect` in `__init__` exactly as Saints does. Started as a
  reasonable placeholder, then taken through the iterative on-charger design workflow below
  until it's right.
- Wire `effect_name = "england"`.

**Extension priority (England must always outrank Saints):**

- `ExtensionEntry` (`app/config.py`) gains an optional `priority: int = 0` field — higher
  wins.
- `load_extensions` (`app/hypervolt/led.py`) sorts its returned `list[ExtensionWrapper]` by
  `(-priority, original_list_index)` — a **stable** sort, so every extension still at the
  default `0` keeps today's exact list-order behaviour. Nothing changes for an existing
  single-extension config, or any config where priorities happen to tie.
- `resolve_theme` itself needs **no changes** at all: both its primary walk and its fallback
  walk already iterate `extensions` in whatever order they're handed, so sorting once inside
  `load_extensions` covers both passes consistently for free.
- `config/config.yml.template`'s example shows `england` configured with a higher `priority`
  than `saints_fc` (e.g. `priority: 1` vs Saints' unset/default `0`), so a deployment that
  follows the shipped example gets the documented England-over-Saints guarantee. The
  guarantee is operator-configured via the shipped default, not framework-hardcoded.
- Raises **an ADR**: explicit per-extension `priority` (default 0, higher wins, stable-sorted,
  applies uniformly to both `resolve_theme` passes) replaces implicit config-list-order as the
  tiebreak semantics. Alternative rejected: hardcoding "England beats Saints" by extension
  name inside `resolve_theme` — rejected because it bakes team-specific logic into core
  resolution, which the framework has deliberately avoided everywhere else (Saints' own
  priority was achieved generically via `resolve_fallback`, never a name check).

### Directory reorg — `extensions/themes/` and `extensions/vehicles/`

- Exactly two new subfolders, both nested under the existing `extensions/` directory:
  `extensions/themes/` and `extensions/vehicles/`. Named after the two provider kinds
  ADR 0017 decided a (not-yet-built) shared extension loader will distinguish
  (`LedThemeProvider` vs `VehicleProvider`) — **not** named after this repo's separate,
  unrelated top-level `themes/` directory (YAML colour data), which this reorg does not touch
  at all.
  `themes/saints_fc.yaml`, `themes/valentines.yaml`, `themes/england.yaml`, and
  `themes/reference/` all stay exactly where they are.
- `extensions/themes/` holds every `LedThemeProvider`: the existing `extensions/saints_fc.py`
  moves to `extensions/themes/saints_fc.py`; `extensions/themes/england.py` and
  `extensions/themes/bonfire.py` join it there.
- `extensions/vehicles/` is reserved for the separate, not-yet-implemented Volvo feature
  (issues #124–#129, ADR 0017). It is **not** created or populated by this work — git doesn't
  track empty directories, so there is nothing to actually commit here beyond documenting the
  convention for whoever picks that feature up next. No Volvo code or issue is touched.
- **Zero loader/mechanism changes are needed for the reorg itself.** `load_extensions` /
  `_load_provider_class` already resolve `extensions_dir / f"{entry.name}.py"` and already
  guard path traversal via `is_relative_to` — an entry name containing a slash (e.g.
  `"themes/saints_fc"`) already resolves correctly into a subfolder through the exact code
  that exists today. `--extensions-dir` keeps pointing at the one `extensions/` root,
  unchanged — this reorg is compatible with ADR 0017's "one directory, one flag" requirement,
  not a reversal of it.
- **This is a hard, documented break** for the one already-shipped extension: its
  `led.extensions` entry name changes from `saints_fc` to `themes/saints_fc`. This follows
  the repo's own established precedent for exactly this situation — ADR 0012's `themes/`
  directory migration was also a hard rename with no fallback, clearly documented — rather
  than inventing new tolerance for this one.
- Doc consequences: `config/config.yml.template`'s `saints_fc` example entry renames to
  `themes/saints_fc` (and gains the `priority` field, showing `england` above it);
  `README.md`'s extensions section updated; a short amendment appended to
  `.agent-docs/adr/0017-extension-loader-generalised-across-provider-kinds.md` (in this
  repo's established pattern of amending ADRs in place — see the superseded/narrowed notes on
  ADR 0006/0007/0010 — rather than rewriting them) recording the two subfolders and the
  correct `extensions/vehicles/` path for whoever picks up Feature 16 next; and
  `.agent-docs/specs/feature-volvo-battery-level.md`'s two `extensions/volvo.py` references
  (lines 153 and 248) get the matching path correction to `extensions/vehicles/volvo.py` — a
  path-reference fix only, not a scope change to that feature.
- Raises **an ADR**: two provider-kind-named subfolders under the one `extensions/`
  directory, zero loader changes required, extension entry names gain a subfolder prefix — a
  hard break for the one already-shipped extension.

### Iterative on-charger design workflow (all three themes)

Colours are never finalised in code review — they're finalised by looking at the real
charger, the same way Saints FC's stripe was designed and later restriped. Each theme in this
feature goes through the same iterative loop before it's considered done:

1. Lay out / adjust the colour design against `themes/reference/charger_led_map.html` (true
   scale, true LED positions, live preview of a `themes/*.yaml` file).
2. Push the current design to the real charger with `scripts/show_led_theme.py`, watch it,
   and repeat step 1 until it looks right on the hardware, not just in the browser preview.
3. For Valentine's Day and England, this loop edits `themes/valentines.yaml` /
   `themes/england.yaml` directly — `scripts/show_led_theme.py` already supports this
   unmodified via its existing `--effect <name>` flag (it resolves `THEMES_DIR/<name>.yaml`
   generically; no script change needed for either).
4. Bonfire Night has no static YAML to edit — its colours come from
   `extensions/themes/bonfire.py`'s own palette/sampling logic. `scripts/show_led_theme.py`
   is extended to also support pushing a **non-static, extension-based** theme: instead of
   loading one YAML once and holding it, it repeatedly calls the extension's `resolve(now)`
   (or a direct frame-generation entry point) and re-pushes, so the flicker itself — not just
   a single static frame — can be watched and tuned live on the real charger.

`scripts/show_led_theme.py` is explicitly **retained and extended** as part of this feature,
not treated as one-off tooling to discard afterwards — it's the mechanism every theme in this
feature (and Saints FC before it) is actually validated through, and each theme issue's
acceptance criteria include having gone through this loop until the operator confirms it
looks right.

## Testing Decisions

Test observable behaviour at the highest existing seam per changed module, following this
branch's established patterns (Saints FC's own test file, the Phase 2 coordinator tests, the
Phase 2 fallback-pass tests).

- **`tests/extensions/themes/test_saints_fc.py`** (moved from `tests/extensions/test_saints_fc.py`,
  mirroring the `extensions/themes/` reorg) — unchanged in substance, just relocated.
- **`tests/extensions/themes/test_england.py`** (new) — follows Saints' test file structure and
  fixture style exactly (frozen clock, `httpx` mock router, kick-off parsing cases for both
  `strTimestamp` and `strTimeLocal`+`dateEventLocal`, window/fallback cases, a DST-transition
  case, `poll_interval_hours` validation).
- **`tests/extensions/themes/test_bonfire.py`** (new) — Nov 5 window activation/deactivation at
  the boundaries; `animated=True` on every `LedTheme` it returns while active; `always_on`
  config passthrough; sampled colour values stay within the orange/red/yellow palette across
  many calls; two consecutive `resolve()` calls return genuinely different LEDs (proves real
  randomisation, not one fixed frame relabelled "animated").
- **`tests/schedule/test_coordinator.py`** — new cases for the animation task lifecycle: an
  animated theme resolving starts the task (repeated `apply_led_state` calls with differing
  `leds` inside a short window, faster than `poll_every_secs` alone would produce); a
  subsequent non-animated resolution cancels the task; the lock actually serialises a
  concurrent normal-cadence call against an in-flight animation-task call (no interleaved or
  partial `charger_client` call observed).
- **`tests/hypervolt/test_led_load_extensions.py`** / **`test_led_resolve_extensions.py`** —
  priority-sorting cases (equal priority preserves list order; explicit priorities reorder
  both the primary and fallback walks consistently) and `animated` surviving the defensive
  copy (mirroring how `always_on` was tested in Phase 2).
- **`tests/test_config.py`** — `ExtensionEntry.priority` defaults to `0`; accepts an explicit
  `int`; rejects a non-`int` (including `bool`, matching the existing bool-rejection
  convention used for `poll_interval_hours`).
- **`tests/themes/test_shipped_themes.py`**-style tests for `themes/valentines.yaml` and
  `themes/england.yaml` — colours load and produce 51 LEDs; specific colour/index assertions
  wait until real colours are designed via the iterative on-charger workflow above — these
  may need loose/placeholder
  assertions initially, the same way Saints' colours were iterated after its mechanism
  shipped.

Prior art: `tests/extensions/test_saints_fc.py`'s fixture-polling/mock-router style;
`tests/schedule/test_coordinator.py`'s existing `_apply_led_state` gate tests;
`tests/hypervolt/test_led_resolve_fallback.py`'s two-pass isolation tests; `tests/test_config.py`'s
existing `poll_interval_hours`-style numeric-field rejection tests.

## Out of Scope

- The Volvo/vehicles feature itself (issues #124–#129) — beyond the one path-reference fix in
  its spec, nothing about that feature is touched, decided, or implemented here.
- Any change to `resolve_theme`'s *primary* walk order (extensions → custom → built-in) —
  unchanged.
- A shared Saints/England base module — explicitly rejected; duplication is accepted.
- Populating `extensions/vehicles/` with any actual code.
- Tuning the bonfire flicker's exact colour distribution/algorithm beyond "orange/red/yellow,
  resampled every cycle" — like the other themes' colours, this is expected to be refined
  empirically once it's running on real hardware.

## Further Notes

- This is a planning-only session: this spec and the accompanying GitHub issues are the
  deliverable. No BDD, code review, or merge happens in this pass.
- No worktree was used for this session — the work happened directly on a branch in the main
  checkout, since only docs are being produced.
- `config/config.yml` holds live credentials and must never be read, echoed, or committed at
  any point in this or a future implementation pass.
- The three themes have materially different complexity: Valentine's Day needs no new
  mechanism at all (pure data, like the existing Saints restripe); Bonfire Night is the
  substantial new-mechanism slice (the `animated` flag + the coordinator's animation task);
  England is mostly repetition of an already-proven pattern plus the new `priority` field.
  Expect the issue breakdown to reflect that spread rather than three equally-sized slices.
