#!/usr/bin/env bash
# Fails the pipeline if total test coverage on this branch is lower than on `main`.
#
# Assumes `uv run pytest --cov=app --cov=extensions --cov-report=json:coverage.json`
# has already run in the current checkout (that JSON is read as "current" coverage
# below), and that the checkout has full history (`fetch-depth: 0`) so `origin/main`
# is available. `--cov=extensions` is harmless against a checkout that predates the
# extensions/ directory -- coverage.py silently contributes nothing for a source path
# that doesn't exist, rather than erroring.
set -euo pipefail

main_worktree=""
cleanup() {
    if [ -n "${main_worktree}" ]; then
        git worktree remove --force "${main_worktree}" 2>/dev/null || rm -rf "${main_worktree}"
    fi
}
trap cleanup EXIT
trap 'echo "::error::check_coverage_diff.sh failed around line ${LINENO} — see the command output above for the underlying error."' ERR

read_percent_covered() {
    python -c "import json; print(json.load(open('$1'))['totals']['percent_covered'])"
}

current_coverage=$(read_percent_covered coverage.json)
echo "Coverage on ${CURRENT_REF_NAME}: ${current_coverage}%"

if [ "${CURRENT_REF_NAME}" = "main" ]; then
    echo "Push is to main — this run establishes the coverage baseline, nothing to compare against."
    exit 0
fi

main_worktree="$(mktemp -d)"
git fetch origin main --quiet
git worktree add --quiet --detach "${main_worktree}" origin/main

if [ -d "${main_worktree}/tests" ] && [ -f "${main_worktree}/uv.lock" ]; then
    (
        cd "${main_worktree}"
        uv sync --frozen --quiet
        uv run pytest --cov=app --cov=extensions --cov-report=json:coverage.json --quiet
    )
    main_coverage=$(read_percent_covered "${main_worktree}/coverage.json")
elif [ -d "${main_worktree}/tests" ]; then
    # One-time transition case: main still predates the Poetry-to-uv migration
    # (no uv.lock yet), and this job no longer installs Poetry on the runner
    # to run main's suite with the old tooling. Same graceful degradation as
    # the "no tests directory" case below -- self-resolving, since main will
    # have uv.lock as soon as this PR merges.
    echo "main predates the Poetry-to-uv migration (no uv.lock yet) — treating baseline coverage as 0% for this one-time transition."
    main_coverage=0
else
    echo "main has no tests directory yet — treating baseline coverage as 0%."
    main_coverage=0
fi
echo "Coverage on main: ${main_coverage}%"

# Run as the condition of an `if` so bash's ERR trap exemption rules apply: a
# deliberate "coverage decreased" exit (code 2, which already printed its own
# specific ::error::) does not also trigger the generic infra-failure message
# below, but any other failure inside this block (e.g. a malformed value) still
# does, since it isn't the exit code we're specifically handling.
if python -c "
current = float('${current_coverage}')
main = float('${main_coverage}')
print(f'Comparing coverage: {current:.2f}% (this branch) vs {main:.2f}% (main)')
if current < main:
    print(f'::error::Test coverage decreased from {main:.2f}% (main) to {current:.2f}% (this branch).')
    raise SystemExit(2)
print('Coverage did not decrease.')
"; then
    exit 0
else
    comparison_exit=$?
    if [ "${comparison_exit}" -eq 2 ]; then
        exit 1
    fi
    echo "::error::check_coverage_diff.sh failed while comparing coverage percentages — see the output above for the underlying error."
    exit "${comparison_exit}"
fi
