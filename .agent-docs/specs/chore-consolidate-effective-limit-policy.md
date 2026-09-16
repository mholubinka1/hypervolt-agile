# Consolidate the effective-limit policy

## Problem Statement

The rule that decides what price the scheduler is actually willing to pay for a charging period
(ADR 0022: the static `price_limit_incl_vat` is always an ultimate ceiling over the dynamic
charging threshold, with caching for cycles where the extension has nothing fresh and a cold-start
skip when nothing is available yet) has no single home in the code. It's asserted in prose across
two ADRs, half-checked at config load time, and executed inline across three separate `Scheduler`
methods (`_current_limit`, `_effective_limit_or_warn`, and duplicated between
`_rebuild_on_replug`/`_rebuild_on_new_prices`), plus an inline validity check on the raw extension
return value that has no equivalent to the validating wrapper the LED Theme Extension path already
has. Anyone asking "why didn't it charge at 14p/kWh" has to read ten files/functions to answer it,
and any future change to the policy (or a future `BehaviourProvider` kind needing the same shape)
risks fixing one call site and missing another.

## Solution

Extract the effective-limit decision into its own module, `ThresholdPolicy`, that owns the whole
ADR 0022 state machine — the min-of-dynamic-and-static comparison, the dynamic-value cache, the
cold-start skip, and the source label used in logs — as a single, independently testable unit.
`Scheduler` holds one `ThresholdPolicy` instance and calls it once per rebuild instead of running
this logic inline. While doing so, also collapse `Scheduler`'s two near-identical rebuild paths
(triggered by a replug vs. a new price fetch) into one shared internal rebuild routine with two
thin trigger methods, and give the charging-threshold `BehaviourProvider` kind a validating wrapper
that mirrors the LED Theme Extension path's `_LedThemeValidatingProvider`, so a misbehaving
extension's bad value is rejected once, symmetrically with how LED already handles it — rather than
via an inline check inside `Scheduler` that only this one provider kind has.

This is a behaviour-preserving refactor: every existing ADR 0021/0022 decision (static-as-ceiling,
`0` as an explicit opt-out, caching, cold-start skip, source labelling, trigger-specific log
wording) stays exactly as it is today. Only where that logic lives, and how a bad dynamic value is
rejected, changes.

## User Stories

1. As a developer changing how the effective charging-price limit is computed, I want that logic
   to live in one module, so that I can find and change it without also hunting for `Scheduler`
   methods, `config.py`, and `behaviour.py` for other pieces of the same rule.
2. As a developer adding a new `BehaviourProvider` kind in future, I want the existing threshold
   kind's validating-wrapper pattern to already exist and mirror LED's, so that I have a
   precedent to follow rather than inventing a third way to defend against a bad extension return
   value.
3. As an operator running the scheduler, I want the effective limit's behaviour (which value wins,
   when the cache is used, when a cycle is skipped, what the rebuild log says) to be unchanged
   after this refactor, so that my running configuration keeps working exactly as it does today.
4. As an operator whose threshold extension misbehaves (returns zero, negative, infinite, or NaN),
   I want that to still be rejected for the cycle and logged, so that a bad value never silently
   becomes the effective limit — even though the log line's exact wording changes to the generic
   provider-failure format.

## Implementation Decisions

- **New module `app/schedule/threshold_policy.py`**: a stateful `ThresholdPolicy` class.
  - Constructed once by `Scheduler` (replacing today's `Scheduler.__init__` seeding of
    `self._static_limit_incl_vat` and `self._cached_dynamic_limit_incl_vat`), holding the static
    limit and owning the dynamic-value cache internally as its own private state — not threaded in
    and out by the caller.
  - Exposes one method taking the fresh raw dynamic value for this cycle (already validated by the
    time it reaches this module — see the validating-wrapper decision below) and returning either
    an effective limit or `None` for the cold-start/no-cap-yet case.
  - The `EffectiveLimit` `NamedTuple` (currently defined in `app/schedule/__init__.py`) moves into
    this module, since it's this module's return type, not `Scheduler`'s.
  - Absorbs today's `_current_limit` and `_effective_limit_or_warn` bodies. The "warn and skip"
    concern splits: the policy module returns `None` for the no-limit-yet case; logging that
    specific warning (with its call-site-specific wording, e.g. "schedule rebuild on car plugged
    in" vs. "schedule update") stays a `Scheduler`-level concern, since the wording is about *why*
    the rebuild is being skipped, which only the caller knows.
  - Does **not** own `config.py`'s `zero_price_limit_requires_a_threshold_extension` validator —
    that stays exactly where it is, since it's a load-time config-shape check ("is
    `extensions.threshold` configured at all"), not a per-cycle runtime computation. The two
    concerns are related by ADR 0022 but categorically different, and merging them would conflate
    load-time validation with runtime policy for no benefit.

- **`Scheduler` (`app/schedule/__init__.py`)**: `_rebuild_on_replug` and `_rebuild_on_new_prices`
  collapse into one shared internal rebuild routine plus two thin trigger methods. Each trigger
  keeps its own pre-check where the two genuinely differ today (the new-prices trigger's "prices
  unchanged, skip" short-circuit; the replug trigger's unconditional rebuild while invalidated) and
  its own log-message context string, threaded into the shared routine as a parameter — so the
  existing trigger-specific wording in both the "no limit yet" warning and the "new schedule
  created" success log is preserved unchanged. Both call sites use the new `ThresholdPolicy`
  instance instead of today's inline `_current_limit`/`_effective_limit_or_warn`, both of which are
  deleted from `Scheduler`.

- **`behaviour.py`**: `load_threshold_extension` changes its internal wiring, not its public
  signature or return type. It still calls `_load_extensions` from `common/extensions.py` and gets
  back a generic `ExtensionWrapper`; it then pulls out that wrapper's raw `.provider`, wraps it in a
  new `_ThresholdValidatingProvider`, and constructs a fresh generic `ExtensionWrapper` around the
  validating provider instead of the raw one. `Scheduler`'s field type
  (`ExtensionWrapper | None` from `common.extensions`) and its call site
  (`threshold_provider.invoke("get_threshold")`) do not change — the validation is inserted
  underneath the existing generic wrapper, not via a new LED-style typed wrapper class. (A typed
  `ThresholdExtensionWrapper` mirroring `hypervolt.led.ExtensionWrapper` was considered and rejected
  — the generic wrapper already gives `Scheduler` everything it needs here.)

- **`_ThresholdValidatingProvider`** (new, in `behaviour.py`): mirrors
  `hypervolt/led.py`'s `_LedThemeValidatingProvider` in structure (wraps a raw provider, delegates
  everything else via `__getattr__`) but validates a `float | None` value's finiteness and
  positivity rather than a type. Its `get_threshold()` raises `ValueError` when the raw value is
  non-finite or non-positive (mirroring how `_validate_led_theme_result` raises `TypeError` on a
  bad type). This is a deliberate behaviour change to the *log wording* only: the generic
  `ExtensionWrapper.invoke()`'s existing exception-isolation/dedup logging catches the raised
  error and logs it in the same format used for any other provider failure
  (`"{kind} {name!r} get_threshold() failed: ValueError: ..."`), replacing today's bespoke
  `Scheduler`-side warning ("Threshold extension returned an invalid value (...); ignoring it for
  this cycle."). The *decision* (reject the value, treat the cycle as if nothing fresh arrived) is
  unchanged — only where it's enforced and how it's logged changes.

- **New ADR** (next number after 0022): records that the effective-limit policy now lives in
  `schedule/threshold_policy.py`, cross-referencing ADR 0021 (which named the `BehaviourProvider`
  protocol) and ADR 0022 (which decided the policy itself) — neither of which records where the
  logic is supposed to live in code. Written after the code change, as a record of the module
  boundary decision, not a new behavioural decision.

## Testing Decisions

Test coverage moves along with the logic it covers, split by seam:

- **New `tests/schedule/test_threshold_policy.py`**: unit tests of `ThresholdPolicy` in isolation,
  with no `Scheduler`, no `AgileClient`, no `ScheduleBuilder` involved. Covers the policy scenarios
  currently tested at the `Scheduler` level: dynamic value wins over static, dynamic clamps to
  static ("static cap"), dynamic equals static (tie-break still labels "dynamic"), static-only (no
  dynamic value, no clamp), fully-deferred (static `0`) with a fresh value, fully-deferred with a
  cached value from a prior call, and the cold-start case (static `0`, no fresh value, no cache
  yet) returning `None`. These are pure input→output policy facts, independent of prices or
  scheduling, so they're tested directly against the module rather than through a full `Scheduler`
  rebuild.

- **`tests/schedule/test_scheduler.py`** (trimmed): keeps genuine integration coverage — that
  `Scheduler` correctly wires `ThresholdPolicy`'s output into `ScheduleBuilder` and into the rebuild
  log line (source label, threshold value at 2dp), that both rebuild triggers (replug and new
  prices) reach the same shared rebuild routine with their own correct log wording, that the
  new-prices trigger's "prices unchanged" short-circuit and the cold-start-must-not-commit-time
  regression (Copilot review, PR #168) still hold once the two methods share an internal path, and
  that a `None` from the extension still falls back to using the static limit for that cycle. The
  four invalid-value tests currently here
  (`test_scheduler_ignores_a_non_finite_or_non_positive_dynamic_threshold` and
  `test_scheduler_skips_the_rebuild_on_a_non_finite_or_non_positive_dynamic_threshold_with_no_static_cap`,
  parametrised over `0.0`, `-5.0`, `inf`, `nan`) move out — `Scheduler` no longer performs this
  check itself once validation relocates to `behaviour.py`, so their premise ("Scheduler must not
  trust a raw value") no longer holds at this seam.

- **`tests/schedule/test_behaviour.py`** (extended): gains the invalid-value coverage moved from
  `test_scheduler.py`, tested at the `load_threshold_extension` seam — a fake provider returning
  `0.0`/`-5.0`/`inf`/`nan` from `get_threshold()`, asserting the returned wrapper's
  `invoke("get_threshold")` returns `None` and that the generic wrapper's own failure-log format
  fires (matching the existing test-file convention here of asserting through `load_threshold_extension`'s
  real return value rather than constructing `ExtensionWrapper` by hand). Follows this file's
  existing pattern of writing a small fake provider module to a `tmp_path` and loading it through
  the real function, rather than reaching into internals.

Prior art for all three: `tests/schedule/test_scheduler.py` and `tests/schedule/test_behaviour.py`
already establish the fake-provider-plus-`caplog` pattern used throughout; `test_threshold_policy.py`
is new but follows the same house style (behaviour-first assertions, no mocking of code this repo
owns).

## Out of Scope

- Moving `config.py`'s zero-limit validator into the new module (stays a load-time schema check).
- Adding a typed `ThresholdExtensionWrapper` class equivalent to `hypervolt.led.ExtensionWrapper`
  (the generic wrapper stays `Scheduler`'s field type; only the provider underneath it changes).
- Unifying the two rebuild paths' log wording into one generic message (trigger-specific wording is
  preserved).
- Any behavioural change to the ADR 0022 policy itself (min-of-two, `0` as opt-out, caching,
  cold-start skip) — this is a pure relocation of existing logic, not a redesign.
- The remaining architecture-review candidates not folded into this piece of work: deepening
  `FuelFinderClient`'s failure signalling (candidate #3), stopping `behaviour.py` from mutating the
  operator's `ExtensionEntry.config` in place (candidate #5), and breaking the `config.py` ↔
  `hypervolt/led.py` import cycle (candidate #6) — each tracked separately in the architecture
  review report, none required by this change.

## Further Notes

Source: architecture-review report generated 2026-09-15 (candidates #1, #2, #4), and a grilling
session that resolved scope (fold #2 and #4 into #1), module shape (stateful class owning its own
cache, new file, no new ADR-mandated redesign), the validating-wrapper mechanism (generic wrapper
retained, validation inserted underneath, raise-and-let-the-generic-wrapper-log rather than a
silent return), and the test-seam split above. All defaults proposed during grilling were accepted
except the typed-wrapper question, where the generic-wrapper option was chosen over mirroring LED's
typed wrapper exactly.
