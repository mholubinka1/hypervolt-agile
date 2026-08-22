#!/usr/bin/env bash
# Fails the pipeline if total test coverage on this branch is lower than on `main`.
#
# Assumes `poetry run pytest --cov=app --cov-report=json:coverage.json` has already
# run in the current checkout (that JSON is read as "current" coverage below), and
# that the checkout has full history (`fetch-depth: 0`) so `origin/main` is available.
set -euo pipefail

read_percent_covered() {
    python -c "import json; print(json.load(open('$1'))['totals']['percent_covered'])"
}

current_coverage=$(read_percent_covered coverage.json)
echo "Coverage on ${CURRENT_REF_NAME}: ${current_coverage}%"

if [ "${CURRENT_REF_NAME}" = "main" ]; then
    echo "Push is to main — this run establishes the coverage baseline, nothing to compare against."
    exit 0
fi

git fetch origin main --quiet
main_worktree="$(mktemp -d)"
git worktree add --quiet --detach "${main_worktree}" origin/main
trap 'git worktree remove --force "${main_worktree}"' EXIT

if [ -d "${main_worktree}/tests" ]; then
    (
        cd "${main_worktree}"
        poetry install --quiet
        poetry run pytest --cov=app --cov-report=json:coverage.json --quiet
    )
    main_coverage=$(read_percent_covered "${main_worktree}/coverage.json")
else
    echo "main has no tests directory yet — treating baseline coverage as 0%."
    main_coverage=0
fi
echo "Coverage on main: ${main_coverage}%"

python -c "
current = float('${current_coverage}')
main = float('${main_coverage}')
print(f'Comparing coverage: {current:.2f}% (this branch) vs {main:.2f}% (main)')
if current < main:
    print(f'::error::Test coverage decreased from {main:.2f}% (main) to {current:.2f}% (this branch).')
    raise SystemExit(1)
print('Coverage did not decrease.')
"
