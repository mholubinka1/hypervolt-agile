# Issues: chore/protocol-handle-result-typing

## Fix HypervoltProtocol.handle result type annotation

**Issue**: #51

**Blocked by**: None

**User stories**: 1, 2

### What to build

Widen `HypervoltProtocol.handle()`'s `result` parameter from `dict` to a type that reflects its real, per-method-varying shape: `dict` for most dispatched methods, `list[dict[str, Any]]` for `sync.snapshot`/`sync.apply` (values are mixed-type — e.g. `{"brightness": 0.25}`, `{"max_current": 32000}`, `{"features": ["super_eco"]}` — not uniformly `str`). Add a short comment on `handle()` explaining which methods deviate and why, with a concrete example inline rather than citing an external file. `_on_sync_response`'s own parameter type had the identical `str`-value mistake pre-existing from the ruff 0.16 upgrade's mechanical typing pass — fixed alongside `handle()` for consistency. No behaviour change.

Correction after Copilot review: the original comment/spec/issue cited `.reference/hypervolt_api_client.py` as the source of truth, but `.reference/` is gitignored and not part of this repository's checkout, making the citation unverifiable for anyone but the author. Replaced with an inline example everywhere it was cited.

### Acceptance criteria

- [ ] `handle()`'s `result` parameter type no longer asserts `dict` for methods that actually return a list
- [ ] `result`'s type does not contain a speculative `Any` where every dispatched method's real shape is already known (`dict` or `list[dict[str, Any]]`)
- [ ] A comment on `handle()` documents the per-method shape variance with a concrete, in-repo-verifiable example
- [ ] `poetry run mypy app` passes
- [ ] Full pre-commit hook suite (ruff, isort --profile black, black, mypy, bandit) passes on the changed file
- [ ] No other file changes beyond `protocol.py`; no behaviour change

---
