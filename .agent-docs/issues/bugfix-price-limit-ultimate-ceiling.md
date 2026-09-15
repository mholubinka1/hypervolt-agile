# Issues: bugfix-price-limit-ultimate-ceiling

## Allow price_limit_incl_vat=0, requiring extensions.threshold when it's set

**Issue**: #166

**Blocked by**: None

**User stories**: 2, 3

### What to build

Change `Schedule.limit`'s validation from `gt=0` to `ge=0` so `price_limit_incl_vat: 0` is
accepted at the field level. Add an `AppConfig`-level validator that rejects `price_limit_incl_vat:
0` unless `extensions.threshold` is also configured, so `0` can never mean "never charge" on a
config with no extension to defer to.

### Acceptance criteria

- [x] `price_limit_incl_vat: 0` with `extensions.threshold` configured loads successfully.
- [x] `price_limit_incl_vat: 0` with no `extensions` block (or `extensions.threshold` unset) raises
      a `ValidationError` at config load, with a message explaining why.
- [x] `price_limit_incl_vat` still rejects negative values and values above 100.
- [x] The existing `("price_limit_incl_vat", 0)` case is removed from
      `test_schedule_rejects_out_of_range_values` (0 is now valid at the `Schedule` level alone).

---

## Static price limit caps the dynamic threshold, with cache fallback, cold-start skip, and source-labeled logging

**Issue**: #167

**Blocked by**: #166

**User stories**: 1, 4, 5, 6

### What to build

Change `Scheduler`'s limit computation so the static `price_limit_incl_vat` is always an ultimate
ceiling: when both a static (non-zero) limit and a fresh dynamic value are available, use whichever
is lower. When the static limit is `0`, use the fresh dynamic value if available, otherwise the last
successfully computed dynamic value (cached across cycles), and if neither is available yet (cold
start), skip building a schedule this cycle and retry on the next one rather than guessing a limit.
Both schedule-rebuild log lines state which source produced the effective limit (static, dynamic,
static cap, or cached dynamic). Update the README and `config/config.yml.template` comments
describing `price_limit_incl_vat` and `extensions.threshold` to reflect this new cap/defer
semantics, replacing the now-inaccurate "static value is the fallback" description.

### Acceptance criteria

- [x] Static limit lower than the dynamic value: the static value is used and the log states
      `"static cap"` as the source.
- [x] Dynamic value lower than (or equal to) the static value: the dynamic value is used and the
      log states `"dynamic"` as the source.
- [x] No provider configured, or provider returns no fresh value this cycle, with a non-zero static
      limit: the static value is used and the log states `"static"` as the source.
- [x] Static limit `0`, provider returns a fresh value: that value is used and cached; log states
      `"dynamic"`.
- [x] Static limit `0`, provider returns no fresh value this cycle but a value was cached from an
      earlier cycle: the cached value is used; log states `"cached dynamic"`.
- [x] Static limit `0`, provider has never returned a fresh value (cold start, nothing cached): the
      scheduler skips building a schedule this cycle without raising, and retries automatically on
      the next cycle.
- [x] README and `config/config.yml.template` no longer describe `price_limit_incl_vat` as the
      dynamic threshold's fallback value; they describe it as an always-applied ultimate cap, with
      `0` explained as the full-defer opt-out.

---
