# Issues: chore-introduce-test-coverage

> Work complete — PR ready to merge.

## Test infrastructure and seeded pure-logic coverage — [#78](https://github.com/mholubinka1/hypervolt-agile/issues/78)

**Blocked by**: None

**User stories**: 1, 4

### What to build

Add pytest (with pytest-asyncio and pytest-cov) as the test framework, configure it and coverage
reporting in `pyproject.toml`, and seed a `tests/` directory mirroring `app/`'s structure with
unit tests for the codebase's pure-logic modules: `common/utils.py`, `common/decorator.py`,
`common/model.py`, `config.py`, and `schedule/builder.py`. Exclude `tests/` from the mypy and
bandit pre-commit hooks (matching the pre-existing mypy exclude intent) since both produce false
positives on test code — mypy can't resolve `app`-rooted imports in isolation, bandit flags
`assert` and fixture secrets by default.

### Acceptance criteria

- [x] `poetry run pytest --cov=app --cov-report=term-missing` runs green locally
- [x] Tests cover: config load success, missing section, missing file, blank credentials,
      out-of-range schedule fields
- [x] Tests cover: schedule builder merging contiguous cheapest periods, keeping non-contiguous
      periods separate, returning nothing when no price is under the limit, rounding up
      fractional half-hour durations
- [x] Tests cover: retry decorator recovering after transient failures and raising after
      exhausting attempts
- [x] Tests cover: `ChargeSession.format` for same-day, overnight, and DST-crossing sessions
- [x] `poetry run pre-commit run --all-files` passes

---

## CI coverage-diff gate — [#79](https://github.com/mholubinka1/hypervolt-agile/issues/79)

**Blocked by**: [#78](https://github.com/mholubinka1/hypervolt-agile/issues/78)

**User stories**: 2, 3

### What to build

A CI step in `ci-checks.yml` that runs the test suite with coverage, then compares the current
branch's total coverage percentage against a live `git worktree` checkout of `origin/main`
(running main's own test suite there too), failing the pipeline only if coverage has decreased.
A push directly to `main` skips the comparison since it establishes the new baseline. See ADR
0002 for why this compares against a live worktree rather than a stored baseline file.

### Acceptance criteria

- [x] CI runs `pytest --cov=app` and produces a JSON coverage report
- [x] A branch with coverage >= main's passes the check
- [x] A branch with coverage < main's fails the check with a clear error message
- [x] A push to `main` itself always passes the check
- [x] Verified by direct local execution against real `origin/main` (no pytest test for the
      script itself — see spec's Testing Decisions)

---
