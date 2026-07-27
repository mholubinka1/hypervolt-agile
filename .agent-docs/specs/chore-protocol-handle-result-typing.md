# Fix misleading result type on HypervoltProtocol.handle

## Problem Statement

`HypervoltProtocol.handle()` declares its `result` parameter as `dict`, but that's factually wrong for two of the fourteen methods it dispatches: `sync.snapshot` and `sync.apply` responses are actually a `list` of single-key dicts (confirmed against `.reference/hypervolt_api_client.py`, the original Home Assistant integration this codebase is ported from). The mismatch was flagged by GitHub Copilot as a low-confidence review comment on PR #49. It isn't a live bug — `_on_sync_response` already handles the list correctly at runtime — but the type signature misleads anyone reading or extending the dispatch table, and mypy can't catch the discrepancy because the `_handlers` dict's value type (`Callable[..., Awaitable[None]]`) doesn't check individual handler signatures against it.

## Solution

Widen `handle()`'s `result` parameter to accurately reflect that its shape varies by method — `dict` for most, `list[dict[str, str]]` for the two sync methods, and potentially other JSON shapes the Hypervolt API could send. A short comment on the method documents which methods return which shape and why the type is permissive. No behaviour changes.

## User Stories

1. As a maintainer reading `HypervoltProtocol.handle()`, I want its type signature to reflect the real, per-method-varying shape of `result`, so that I don't assume every handler receives a plain `dict` when extending or debugging the dispatch table.
2. As a maintainer, I want a comment explaining *why* the type is permissive and which methods deviate, so that the reasoning doesn't have to be re-derived from `.reference/` each time.

## Implementation Decisions

- **Module changed**: `app/hypervolt/client/protocol.py` only.
- **Interface change**: `handle(self, method: str, result: dict, id: str | None)` → `handle(self, method: str, result: dict | list[dict[str, str]] | Any, id: str | None)`.
  - Rejected: a fully type-safe per-method dispatch (e.g. overloads or a typed protocol keyed by method) that would let mypy verify each handler's parameter type — ruled out as unnecessary machinery for a 14-entry internal dispatch table.
  - Rejected: annotating as plain `Any` — chosen against in favour of a union that documents the two concretely-known shapes (`dict`, `list[dict[str, str]]`) alongside `Any` for anything else, so the annotation itself carries information for a reader rather than erasing it.
- Individual handler methods (`_on_sync_response`, `_on_login_response`, etc.) keep their existing, already-accurate parameter types — only the dispatch entry point's signature was wrong.
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
