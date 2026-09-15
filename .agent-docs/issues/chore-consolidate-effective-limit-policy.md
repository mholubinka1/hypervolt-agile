# Issues: chore-consolidate-effective-limit-policy

> Work complete — PR ready to merge.

## Extract the effective-limit policy into ThresholdPolicy

**GitHub issue**: #171

**Blocked by**: None

**User stories**: 1, 3

### What to build

A new `ThresholdPolicy` module owning the whole ADR 0022 state machine — the min-of-dynamic-and-static
comparison, the dynamic-value cache, the cold-start skip, and the source label used in rebuild logs — as
a single, independently testable unit. It's a stateful class: constructed once with the static limit,
holding the dynamic-value cache as its own private state rather than having it threaded in and out by a
caller. It exposes one method that takes the fresh raw dynamic value for the current cycle and returns
either an effective limit (value plus source label) or nothing, for the cold-start/no-cap-yet case.

`Scheduler` is rewired to hold one instance of this policy and call it once per rebuild, replacing its
current inline computation and the separate "warn and skip" helper. The decision of *what* to log when
there's no limit yet stays with `Scheduler` (the wording differs by which trigger is skipping), but the
decision of *whether* there's a limit at all moves entirely into the new module.

This is a pure relocation, not a redesign — every scenario the policy handles today (dynamic wins,
static acts as a ceiling, deferring fully to the extension when the static limit is an explicit opt-out,
falling back to a cached value, and skipping when nothing is available yet) must produce byte-for-byte
the same rebuild logs and schedules as before.

### Acceptance criteria

- [x] Given a fresh dynamic value below the static limit, the effective limit is the dynamic value,
      labelled "dynamic".
- [x] Given a fresh dynamic value above the static limit, the effective limit is the static value,
      labelled "static cap".
- [x] Given a fresh dynamic value exactly equal to the static limit, the effective limit is labelled
      "dynamic", not "static cap".
- [x] Given no fresh dynamic value and a non-zero static limit, the effective limit is the static value,
      labelled "static".
- [x] Given the static limit is the explicit opt-out value and a fresh dynamic value is available, the
      effective limit is the dynamic value as-is, with no clamp.
- [x] Given the static limit is the explicit opt-out value, no fresh dynamic value this cycle, but a
      dynamic value cached from a prior cycle, the effective limit is the cached value, labelled "cached
      dynamic".
- [x] Given the static limit is the explicit opt-out value, no fresh dynamic value, and nothing cached
      yet (cold start), the policy reports no limit is available.
- [x] `Scheduler`'s rebuild log still states the correct source label and the threshold value at the same
      precision as today.
- [x] All existing `Scheduler`-level tests covering these scenarios continue to pass unmodified in
      behaviour (test location may move — see the "merge rebuild paths" and "validating wrapper" issues
      for where).

---

## Merge Scheduler's duplicated rebuild paths

**GitHub issue**: #172

**Blocked by**: #171

**User stories**: 1

### What to build

`Scheduler`'s two rebuild triggers — one fired by the car being plugged in, one fired by a new set of
Agile prices arriving — currently duplicate the same fetch-prices → get-limit → build-schedule → log
sequence almost line for line. Collapse this into one shared internal rebuild routine, with the two
triggers reduced to thin methods that each keep only what's genuinely different between them: the
new-prices trigger's short-circuit when the price horizon hasn't actually changed, and each trigger's own
log wording (e.g. "schedule rebuild on car plugged in" vs. "schedule update", and their respective
success-log phrasing). Both triggers call the newly-extracted `ThresholdPolicy` from #171.

This depends on #171 because collapsing the duplicated fetch→limit→build→log sequence is
far simpler once both call sites already share the same one-line call into the policy, rather than each
still carrying its own inline limit computation.

### Acceptance criteria

- [x] A car-plugged-in rebuild still fetches prices, computes the limit, builds the schedule, and logs
      "New Schedule created on car plugged in..." with the correct source and value, unchanged from
      today.
- [x] A new-prices rebuild with an unchanged price horizon still skips without doing any further work
      (existing short-circuit preserved).
- [x] A new-prices rebuild with a changed price horizon still fetches, computes, builds, and logs "New
      schedule created..." with the correct source and value, unchanged from today.
- [x] The existing regression (a cold-start skip on the new-prices trigger must not commit the new price
      horizon) still holds — an unchanged-but-not-yet-limited price horizon still re-asks the policy on
      the following cycle rather than being mistaken for "already seen".
- [x] Both triggers demonstrably share one internal rebuild path (not just visually similar code) —
      provable by a single point of change affecting both, e.g. via a shared private method.

---

## Add a validating wrapper for the charging threshold provider

**GitHub issue**: #173

**Blocked by**: #171

**User stories**: 2, 4

### What to build

Give the charging-threshold `BehaviourProvider` kind a validating wrapper that mirrors the LED Theme
Extension path's existing validating wrapper, closing the asymmetry where LED already centralises its
"is this a well-formed result" check and threshold does not. The extension loader for this provider kind
gets back the generic extension wrapper as it does today, then wraps the raw provider underneath it in a
new validating layer before handing it onward — so the type `Scheduler` depends on and the way it invokes
the provider don't change, only what's guarding the value underneath.

The validating layer rejects a non-finite or non-positive value by raising, which the generic wrapper's
existing per-provider failure isolation and log-deduplication already knows how to handle — so a bad
value is logged in the same generic "provider call failed" format used for any other provider exception,
rather than a bespoke message specific to this one provider kind. The rejection decision itself (treat a
bad value as if nothing fresh arrived this cycle) is unchanged; only where it's enforced and how it's
logged changes. This also means `ThresholdPolicy` from #171 no longer needs its own
finite/positive check — the value reaching it is already trustworthy — so that check is removed from the
policy as part of this change.

### Acceptance criteria

- [x] A provider returning zero, a negative number, infinity, or NaN is rejected: the cycle is treated as
      if the provider had returned nothing fresh, exactly as it is today.
- [x] The rejection is now logged in the generic provider-failure format (naming the kind, the provider
      name, the method, and the exception), not the old bespoke "Threshold extension returned an invalid
      value" message.
- [x] A provider returning a valid positive finite value is unaffected and passes through unchanged.
- [x] Repeated identical invalid values across consecutive cycles are still deduplicated in the log
      exactly as any other repeated provider failure is today (no double-logging regression).
- [x] `ThresholdPolicy`'s own finite/positive check is removed — coverage for invalid values now lives at
      this loader/wrapper seam instead.

---

## Record the module boundary in a new ADR

**GitHub issue**: #174

**Blocked by**: #171, #172, #173

**User stories**: 1

### What to build

A new ADR documenting that the effective-limit policy now lives in one module, cross-referencing the two
existing ADRs that describe the policy itself and the provider-kind naming, neither of which records
where the logic is supposed to live in code. Written after the code changes land, as a record of the
module-boundary decision — not a proposal for further behavioural change.

### Acceptance criteria

- [x] A new ADR file exists, numbered immediately after the most recent existing ADR.
- [x] It names the module now responsible for the effective-limit policy and cross-references both prior
      ADRs it relates to.
- [x] It accurately reflects the final code structure after #171, #172, and #173 have merged, not a proposal.

---
