# The static `price_limit_incl_vat` is always an ultimate ceiling on the dynamic charging threshold, not just its fallback value

ADR 0021 and the original #159 implementation had the dynamic threshold provider fully *override*
`price_limit_incl_vat` whenever it returned a fresh value — the static config only mattered as a
fallback for cycles where the provider had nothing to say. In practice this meant a misbehaving or
miscalibrated extension (bad fuel price data, a station mix that skews the breakeven price up) could
push the effective limit arbitrarily high, with no static backstop protecting the operator from
overpaying — the opposite of what an "ultimate price willing to pay" setting is for.

Decision: `price_limit_incl_vat` now always caps the effective limit — `effective = min(dynamic,
static)` whenever both are available, so the dynamic threshold can only make charging *more*
conservative than the static cap, never less. `price_limit_incl_vat: 0` is repurposed as an explicit
opt-out of the cap ("defer fully to the extension") rather than a value that would otherwise be
rejected by validation (`gt=0` becomes `ge=0`); to keep `0` from silently meaning "never charge" on a
config with no extension configured, `AppConfig` now rejects `price_limit_incl_vat: 0` at load time
unless `extensions.threshold` is also set. When the extension has no fresh value this cycle and
`price_limit_incl_vat` is `0`, the scheduler falls back to its last cached fresh value rather than
treating the cycle as "no limit" (which combined with `0` would block all charging) — and if no
cached value exists yet (cold start), the scheduler skips building a schedule for that cycle and
retries on the next one, rather than guessing a limit.

This is hard to reverse once operators have set `price_limit_incl_vat: 0` expecting full extension
control, surprising without this context (a reader would otherwise assume the existing
override-with-fallback behaviour, which is the opposite of "ultimate ceiling"), and was a genuine
trade-off against two simpler alternatives that were rejected: keeping full override (rejected — no
safety backstop against a misbehaving extension) and rejecting `0` outright (rejected — the user
explicitly wants a way to hand full control to the extension).
