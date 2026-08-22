# Introduce pytest test coverage and a CI coverage-diff gate

## Problem Statement

This repo has never had automated tests — verification was through manual execution only (a
deliberate prior policy). That leaves regressions in business logic (schedule building, config
validation, retry behaviour, price formatting) undetected until they're run against a real
charger, and there is no mechanism to stop a future PR from silently deleting or weakening
whatever test coverage does exist.

## Solution

Introduce pytest as the test framework, seed unit tests for the codebase's pure-logic modules,
and add a CI check that fails the pipeline if a branch's total coverage percentage is lower than
`main`'s. The check is a ratchet, not a fixed threshold: it doesn't require the whole codebase
to reach any particular number today, only that coverage never regresses from where `main`
currently stands.

## User Stories

1. As the maintainer, I want `poetry run pytest` to run a real test suite locally, so that I can
   verify business-logic changes without manually running the app against a live charger.
2. As the maintainer, I want CI to fail if my branch's total test coverage is lower than what's
   currently on `main`, so that coverage can only go up over time, never quietly down.
3. As the maintainer, I want CI to still pass when coverage stays the same or increases, so that
   the gate never blocks unrelated work that doesn't touch tested code.
4. As the maintainer, I want the seeded tests to cover the modules most likely to contain real
   bugs (schedule building, config validation, retry logic, price/session formatting) rather than
   padding coverage with trivial tests, so the suite has genuine value from day one.

## Implementation Decisions

- **Dependencies**: `pytest`, `pytest-asyncio`, `pytest-cov` added to `[tool.poetry.group.dev.dependencies]`.
- **Pytest config** (`[tool.pytest.ini_options]` in `pyproject.toml`): `pythonpath = ["app"]`
  (mirrors how `app/` is already added to `sys.path` when run directly — see `app/main.py`),
  `testpaths = ["tests"]`, `asyncio_mode = "auto"`.
- **Coverage config** (`[tool.coverage.run]` / `[tool.coverage.report]`): source is `app`, branch
  coverage on, missing lines shown in terminal reports.
- **Test layout**: `tests/` mirrors the `app/` package structure (`tests/common/`,
  `tests/schedule/`, `tests/test_config.py`).
- **CI** (`.github/workflows/ci-checks.yml`): after `poetry install`, run
  `pytest --cov=app --cov-report=term-missing --cov-report=json:coverage.json`, then run
  `.github/scripts/check_coverage_diff.sh` (see ADR
  [0002](../adr/0002-coverage-ratchet-via-live-main-worktree.md) for why this compares against a
  live worktree of `origin/main` rather than a stored baseline). On a push to `main` itself, the
  script exits 0 immediately — that push *is* the new baseline, nothing to compare against.
- **Lint/type-check scope**: `tests/` is excluded from the mypy and bandit pre-commit hooks
  (`.pre-commit-config.yaml`), matching the `exclude=["tests", ...]` already present in
  `[tool.mypy]` before this change. isort/black/ruff still run over `tests/` as normal — this
  exclusion is specifically because mypy can't resolve `app`-rooted imports from a file invoked
  in isolation (no `MYPYPATH`), and bandit's default profile flags `assert` (B101) and fixture
  strings like `"secret"`/`"sk_test"` (B105) as findings, both false positives in test code by
  design.
- **coverage.json** is a generated artifact — added to `.gitignore`, never committed.

## Testing Decisions

- The seam for the seeded tests is each module's existing public API — no new seams introduced.
  Modules covered: `common/utils.py` (`is_null_or_empty`), `common/decorator.py` (`retry`,
  exercised with `retry_delay=0` to avoid real sleeps), `common/model.py`
  (`ChargeSession.format`, including a same-day vs. overnight vs. DST-crossing case), `config.py`
  (`ConfigLoader`, `Octopus`, `Schedule` — valid load, missing section, missing file, blank
  credentials, out-of-range schedule fields), `schedule/builder.py` (`ScheduleBuilder.build` —
  contiguous-period merging, non-contiguous sessions, nothing under the price limit, fractional
  duration rounding).
- I/O-heavy modules (`hypervolt/charger.py`, `client/protocol.py`, `client/rest.py`,
  `client/websocket.py`, `octopus/client.py`, `schedule/coordinator.py`, `main.py`) are
  deliberately **not** tested in this change — see Out of Scope.
- `.github/scripts/check_coverage_diff.sh` is verified by direct execution, not a pytest test:
  it was run locally with `CURRENT_REF_NAME` set to this branch against the real `origin/main`
  (main has no tests yet, so it correctly degraded to a 0% baseline and passed), and the pass/fail
  comparison arithmetic was sanity-checked directly. This matches how the repo already verifies
  its other CI shell/YAML tooling — through execution, not a test framework.

## Out of Scope

- Tests for the I/O-heavy modules listed above — separate, incremental follow-up work, one module
  at a time, once this infra is in place.
- Any fixed coverage percentage threshold (e.g. the 80% figure in `.agent-docs/agent.md`'s
  generic Testing Discipline standard) — the ratchet enforces "never decreases," not "must reach
  X%." Reaching 80% is a natural long-run consequence of the ratchet plus future module coverage,
  not a gate this PR enforces directly.
- Type-checking or security-scanning `tests/` itself with mypy/bandit.
- Updating the local-only `FEATURES.md`'s several stale "no unit tests are written in this
  codebase" lines — that file is gitignored and personal, left for the user to update at their
  convenience.

## Further Notes

This reverses a previous, deliberate "no tests" policy for this repo. The seeded suite currently
covers ~18% of `app/` — expected and acceptable, since the gate is a ratchet rather than a
threshold (see Implementation Decisions).
