# Fix misleading result type on HypervoltProtocol.handle

## Problem Statement

`HypervoltProtocol.handle()` declares its `result` parameter as `dict`, but that's factually wrong for two of the fourteen methods it dispatches: `sync.snapshot` and `sync.apply` responses are actually a `list` of single-key dicts with heterogeneous value types, e.g. `[{"brightness": 0.25}, {"lock_state": "unlocked"}, {"max_current": 32000}, {"features": ["super_eco"]}, {"random_start": True}]` — a `float`, a `str`, an `int`, a `list`, and a `bool` in a single response (this codebase is a Python reimplementation of an existing Home Assistant Hypervolt integration; the shape reflects the real websocket API, not an artifact of the port). The mismatch was flagged by GitHub Copilot as a low-confidence review comment on PR #49. It isn't a live bug — `_on_sync_response` already handles the list correctly at runtime — but the type signature misleads anyone reading or extending the dispatch table, and mypy can't catch the discrepancy because the `_handlers` dict's value type (`Callable[..., Awaitable[None]]`) doesn't check individual handler signatures against it.

## Solution

Widen `handle()`'s `result` parameter to accurately reflect that its shape varies by method — `dict` for most, `list[dict[str, Any]]` for the two sync methods (the observed wire values are mixed-type — float, str, int, list, bool — so `str` alone would be a second dishonest type). A short comment on the method documents which methods return which shape, with a concrete example inline so the claim is verifiable without an external reference. No behaviour changes.

## User Stories

1. As a maintainer reading `HypervoltProtocol.handle()`, I want its type signature to reflect the real, per-method-varying shape of `result`, so that I don't assume every handler receives a plain `dict` when extending or debugging the dispatch table.
2. As a maintainer, I want a comment explaining *why* the type is permissive and which methods deviate, with a concrete example I can verify without needing access to an external or local-only file.

## Implementation Decisions

- **Application code changed**: `app/hypervolt/client/protocol.py` only (this PR also adds its own spec/issue docs under `.agent-docs/`, per the repo's standard workflow).
- **Interface change**: `handle(self, method: str, result: dict, id: str | None)` → `handle(self, method: str, result: dict[str, Any] | list[dict[str, Any]], id: str | None)`.
  - Rejected: a fully type-safe per-method dispatch (e.g. overloads or a typed protocol keyed by method) that would let mypy verify each handler's parameter type — ruled out as unnecessary machinery for a 14-entry internal dispatch table.
  - Rejected (caught in review): a top-level `| Any` for "other shapes the API may send" — every one of the 14 dispatched methods needs exactly `dict` or `list[dict[str, Any]]` and nothing else, so a speculative `Any` on top added no real coverage and, since mypy treats `X | Any` as absorbed into `Any` for assignability, gave no more type-checking power than the plain-`Any` alternative the spec explicitly rejected.
  - Rejected (caught in review): `list[dict[str, str]]` for the sync methods — the observed wire example shows values of mixed types (float, str, int, list, bool), so `str` was a second dishonest type introduced by the same PR meant to remove one. Corrected to `list[dict[str, Any]]`.
  - Also caught in review: the `dict` arm was left unparameterised while the `list` arm was fully parameterised. JSON object keys are always `str`, so tightened to `dict[str, Any]` for consistency.
  - Also caught in review: the original comment and this spec cited `.reference/hypervolt_api_client.py` as the source of truth, but `.reference/` is gitignored and not part of this repository's checkout — an unverifiable citation for anyone but the author. Replaced with an inline example of the actual observed payload shape (see Problem Statement above) so the claim stands on its own.
- `_on_sync_response`'s own parameter type had the identical `list[dict[str, str]]` mistake (pre-existing, from the ruff 0.16 upgrade's mechanical typing pass) — fixed to `list[dict[str, Any]]` alongside `handle()` for consistency, since leaving it wrong right next to the corrected line would reintroduce the exact problem this change fixes.
- `.agent-docs/context.md` is not updated: the varying wire-format shape is an implementation detail of the protocol, not domain terminology, and the glossary format explicitly excludes implementation details.
- No ADR: the change is trivially reversible (a type annotation and a comment), so it doesn't meet the "hard to reverse" bar.

## Testing Decisions

- No unit tests, consistent with this project's established convention (verification is through tooling and execution, not test suites).
- Verification seam: `poetry run mypy app` must pass, plus the full pre-commit hook suite (ruff, isort --profile black, black, mypy, bandit) must pass on the changed file.
- No runtime/functional test is needed because this change carries no behaviour change — it only corrects a static type annotation.

## Out of Scope

- Making the `_handlers` dispatch table itself type-safe per method (see rejected alternative above).
- Any other Copilot suggestions from PR #49 (there were none — this was the only comment, and it was suppressed as low-confidence rather than posted as an actionable thread).
- Any change to runtime behaviour of message handling.

## Further Notes

Originated from a Copilot review comment on PR #49 (`chore/ruff-0-16-upgrade`), which was suppressed due to low confidence and therefore didn't surface as an actionable review thread during the `address-copilot-comments` loop. Investigated separately per the user's request after that PR merged.
