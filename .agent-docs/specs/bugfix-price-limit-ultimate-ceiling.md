# Static price limit becomes an ultimate ceiling on the dynamic charging threshold

## Problem Statement

An operator with the dynamic charging threshold extension (issue #159, PR #164) configured has no
static backstop: today, whenever the extension returns a fresh value, it fully replaces
`price_limit_incl_vat` for that cycle. If the extension miscalculates (bad fuel-price data, an
unrepresentative station mix, an API glitch that returns a plausible-but-wrong number), the
effective limit can drift arbitrarily high with nothing capping it — the opposite of what
`price_limit_incl_vat` is meant to guarantee: the ultimate price the operator is willing to pay.

## Solution

`price_limit_incl_vat` always caps the effective charging limit, whether or not the dynamic
threshold extension is active. The extension can only make charging *more* conservative than this
cap, never less. An operator who wants the extension to have full, uncapped control can now set
`price_limit_incl_vat: 0` to opt out of the cap entirely.

## User Stories

1. As an operator with the dynamic threshold extension enabled, I want `price_limit_incl_vat` to
   remain a hard upper bound, so that a miscalculated dynamic threshold can never make my car charge
   above the price I've said I'm willing to pay.
2. As an operator who trusts the dynamic threshold extension completely, I want to set
   `price_limit_incl_vat: 0` to hand it full, uncapped control, so that I don't have to also
   maintain a static number that's just noise for my setup.
3. As an operator, I want the config to reject `price_limit_incl_vat: 0` when I haven't configured
   `extensions.threshold`, so that I get a clear startup error instead of a scheduler that silently
   never charges.
4. As an operator, I want the effective limit to keep using the last value the extension actually
   computed (rather than treating a transient gap as "no limit") when `price_limit_incl_vat` is `0`
   and the extension has no fresh value this cycle, so a brief fuel-price API hiccup doesn't stop
   charging outright.
5. As an operator, I want the scheduler to skip building a schedule (and retry next cycle) rather
   than guess a limit, on the very first cycle after startup where `price_limit_incl_vat` is `0`
   and the extension hasn't produced a value yet.
6. As an operator reading the rebuild log, I want to see which source produced the effective limit
   (static, dynamic, static cap, or cached dynamic), so I can tell at a glance whether the cap or
   the extension is actually governing my charging price right now.

## Implementation Decisions

- `Schedule.limit` (`app/config.py`): `Field(..., alias="price_limit_incl_vat", gt=0, le=100)` →
  `ge=0` (upper bound and other fields unchanged). `0` is now a valid schedule limit.
- `AppConfig` gains a `model_validator(mode="after")` rejecting `schedule.limit == 0` when
  `extensions is None or extensions.threshold is None` — `0` only has meaning when there's an
  extension to defer to.
- `Scheduler.__init__` (`app/schedule/__init__.py`) gains `self._cached_dynamic_limit_incl_vat:
  float | None = None`, updated every time the threshold provider returns a fresh (non-`None`)
  value, regardless of whether the static limit is `0`.
- `Scheduler._current_limit()` is rewritten to return an `EffectiveLimit` NamedTuple (`incl_vat`,
  `exc_vat`, `source`) or `None` when no effective limit can be determined yet:
  - Fetch the provider's fresh value first (if a provider is configured). A fresh value that isn't
    finite and positive (`0`, negative, `inf`, `NaN` — a misbehaving or buggy provider) is discarded
    and logged as a warning, treated identically to the provider returning no fresh value at all,
    before either caching or use — the static ceiling this feature exists to guarantee must not be
    underminable by an unvalidated provider return.
  - Otherwise update the cache with the fresh value.
  - `static_limit == 0`: use the fresh value if present (source `"dynamic"`); else the cached value
    if present (source `"cached dynamic"`); else return `None` (no limit determinable this cycle).
  - `static_limit > 0` and a fresh dynamic value is present: `min(dynamic, static)` — source
    `"dynamic"` when the dynamic value wins, `"static cap"` when the static value clamps it.
  - `static_limit > 0` and no fresh dynamic value: use the static value, source `"static"`.
- The two rebuild call sites share this "no limit yet, warn and skip" path via a
  `_effective_limit_or_warn` helper, parameterised only by the action description in the warning.
- `Scheduler._rebuild_on_replug` and `_rebuild_on_new_prices` both handle a `None` result from
  `_current_limit()` by logging a warning and returning without building a schedule (leaving
  `_invalidated` / the update timer such that the next cycle retries naturally — no new retry
  machinery needed). Both rebuild log lines gain the source label alongside the existing
  `limit {X:.2f}p/kWh incl VAT` text.
- No change to `ScheduleBuilder`, the threshold extension itself, or the LED-side config models.

## Testing Decisions

- Scheduler-level tests in `tests/schedule/test_scheduler.py`, following the existing pattern
  (`AppConfig` + a fake `get_threshold()` provider wrapped in `ExtensionWrapper`, asserting on
  `scheduler.schedule` and `caplog` records): cover the clamp (dynamic > static → static wins),
  the pass-through (dynamic <= static → dynamic wins), the zero-defers-and-caches case (static=0,
  fresh value then a later cycle with no fresh value uses the cache), the cold-start skip (static=0,
  no cached value yet → schedule stays empty and no exception), and the rebuild log naming the
  correct source in each case, plus a non-finite/non-positive dynamic value (`0`, negative, `inf`,
  `NaN`) being discarded rather than used or cached, both with a non-zero static limit (falls back
  to static) and with `price_limit_incl_vat: 0` and nothing cached yet (falls through to the
  cold-start skip).
- Config-level tests in `tests/test_config.py`: `price_limit_incl_vat: 0` with no
  `extensions.threshold` configured raises `ValidationError`; `price_limit_incl_vat: 0` with
  `extensions.threshold` configured loads successfully. The existing
  `test_schedule_rejects_out_of_range_values` parametrized case for `("price_limit_incl_vat", 0)`
  is removed (0 is now a valid `Schedule`-level value; the rejection moves to the `AppConfig`-level
  tests above, since `Schedule` alone can't know whether an extension is configured).
- Test external behaviour (built schedule, log content, `ValidationError`) — no new tests reach into
  `_current_limit()`'s internals directly.

## Out of Scope

- Any change to the dynamic charging threshold extension itself (`extensions/behaviours/
  dynamic_charging_threshold.py`) or its breakeven-price computation.
- Documenting the dynamic threshold extension's own config block in full in the README (it's
  currently undocumented there, but that's a pre-existing gap from PR #164, not something this
  change needs to fully close) — only the `price_limit_incl_vat` / threshold interaction semantics
  are documented here, in the README and `config/config.yml.template`.
- Deploying this change to the Pi (tracked separately, alongside the rest of the dynamic threshold
  rollout).

## Further Notes

- Follows on from issue #159 / PR #164 (merged) and its config-schema amendment recorded in ADR
  0021. This change's own rationale is recorded in the new ADR 0022
  (`0022-static-price-limit-is-an-ultimate-ceiling-not-a-fallback.md`).
- `.agent-docs/context.md`'s "Dynamic charging threshold" glossary entry has already been updated
  to describe the new cap semantics.
