# The effective-limit policy lives in `schedule/threshold_policy.py`, not scattered across `Scheduler`, `config.py`, and `behaviour.py`

ADR 0021 named the `BehaviourProvider` protocol and ADR 0022 decided that `price_limit_incl_vat`
always acts as an ultimate ceiling over a dynamic threshold extension's value — `effective =
min(dynamic, static)`, with a cache of the last fresh dynamic value and a cold-start skip when
static is the explicit opt-out (`0`) and nothing is available yet, fresh or cached. Neither ADR
records where that logic is supposed to live in code. Before this change it didn't live anywhere
in particular: the decision was inline across `Scheduler._current_limit` and
`Scheduler._effective_limit_or_warn`, duplicated between `_rebuild_on_replug` and
`_rebuild_on_new_prices`, half-asserted in `config.py`'s zero-limit validator, and had no
equivalent to the validating wrapper the LED Theme Extension path already uses to reject a
misbehaving provider's bad return value — an architecture review (2026-09-15) named this as its
top consolidation candidate, citing ten files/functions to read to answer "why didn't it charge at
14p/kWh".

Decision: `ThresholdPolicy` (`app/schedule/threshold_policy.py`) is the single owner of the ADR
0022 state machine — the min-of-dynamic-and-static comparison, the dynamic-value cache, the
cold-start skip, and the source label used in rebuild logs. It's a stateful class constructed once
per `Scheduler` lifetime with the static limit, holding the dynamic-value cache as its own private
state; `Scheduler` calls one method (`effective_limit`) once per rebuild, through a single shared
internal rebuild routine both triggers (car-plugged-in, new-prices) now call, rather than each
computing the limit and rebuilding inline. The charging-threshold `BehaviourProvider` kind also
gained its own `_ThresholdValidatingProvider` (`app/schedule/behaviour.py`), mirroring
`hypervolt/led.py`'s `_LedThemeValidatingProvider`: a non-finite or non-positive value is now
rejected and logged at the extension-loader seam (the generic `ExtensionWrapper`'s existing
per-provider failure isolation), not silently inside the policy.

Two things deliberately stayed where they were. `config.py`'s
`zero_price_limit_requires_a_threshold_extension` validator was not folded into `ThresholdPolicy`
— it enforces config *shape* at load time ("is `extensions.threshold` configured at all"), a
different concern from `ThresholdPolicy`'s per-cycle runtime computation, and merging the two would
conflate load-time validation with runtime policy for no benefit. `Scheduler` also did not gain a
typed threshold wrapper equivalent to `hypervolt.led.ExtensionWrapper` — it still depends on the
generic `common.extensions.ExtensionWrapper` and calls `.invoke("get_threshold")` exactly as
before; only what's wrapped underneath changed.

This is hard to reverse in the sense that matters for a code-organisation ADR: it's not risky to
undo, but silently re-scattering this logic across `Scheduler`, `config.py`, and `behaviour.py`
again — e.g. a future change adding a special case inline in a rebuild method instead of in
`ThresholdPolicy` — would quietly reintroduce the exact problem this refactor fixed, with no error
to catch it. It's also surprising without this context: a reader familiar with ADR 0021/0022's
prose but not this one would have no way to know where the policy those ADRs describe actually
lives, or that a validating wrapper for this provider kind exists and where.
