# Test coverage is enforced as a ratchet against a live checkout of main, not a threshold or a stored baseline file

This repo previously had no automated tests at all (a deliberate prior choice, since reversed).
Rather than requiring a fixed coverage percentage (which would demand backfilling the many
untested I/O-heavy modules before any gate could land) or maintaining a committed baseline file
(which drifts if a merge forgets to update it), `ci-checks.yml` computes coverage for the
current push, then checks out `origin/main` into a temporary `git worktree` inside the same job
and runs *its* test suite too, comparing the two percentages. The pipeline fails only if
coverage is lower than `main`'s current figure — equal or higher always passes. This lets
coverage start low (today, ~18%, seeded on pure-logic modules only) and ratchet upward
incrementally as future PRs touch each module, while guaranteeing the comparison point is
always genuinely what's on `main`, never a value that can silently go stale. The trade-off
accepted is a second `poetry install` + test run per CI execution. When `main` itself has no
`tests/` directory (true only until this PR merges), the baseline degrades to 0% rather than
erroring.
