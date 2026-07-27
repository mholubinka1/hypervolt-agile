# Issues: chore/protocol-handle-result-typing

## Fix HypervoltProtocol.handle result type annotation

**Issue**: #51

**Blocked by**: None

**User stories**: 1, 2

### What to build

Widen `HypervoltProtocol.handle()`'s `result` parameter from `dict` to a type that reflects its real, per-method-varying shape: `dict` for most dispatched methods, `list[dict[str, str]]` for `sync.snapshot`/`sync.apply`, and `Any` for anything else the API might send. Add a short comment on `handle()` explaining which methods deviate and why, referencing `.reference/hypervolt_api_client.py` as the source of truth for the wire format. No behaviour change — individual handler signatures are already correct and untouched.

### Acceptance criteria

- [ ] `handle()`'s `result` parameter type no longer asserts `dict` for methods that actually return a list
- [ ] A comment on `handle()` documents the per-method shape variance
- [ ] `poetry run mypy app` passes
- [ ] Full pre-commit hook suite (ruff, isort --profile black, black, mypy, bandit) passes on the changed file
- [ ] No other file changes; no behaviour change

---
