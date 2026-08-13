# CI: Node 24 Action Runtimes + Docker Login/Push Retry (cross-repo)

Reusable spec for any repo in the CI/CD & Copilot pipeline hardening series that has already
had its security hardening done (trigger fix, CODEOWNERS, ruleset) but still runs old,
Node20-runtime GitHub Actions and has no retry around Docker Hub login/push. Proven pattern:
`hypervolt-agile` PR #68 (merged 2026-08-11).

## Problem Statement

CI workflows emit "Node.js 20 is deprecated" warnings because several pinned action versions
still resolve to releases whose `action.yml` declares `using: node20`. GitHub is removing
Node 20 support from Actions runners entirely on 2026-09-16 — after that date these actions
stop working outright, not just warn. Separately, the Docker Hub login/build/push steps have
no retry: a transient DNS blip or a momentary Docker Hub auth hiccup fails the whole build with
no automatic recovery, requiring a manual re-push to notice and fix.

## Solution

1. Bump every action pin that resolves to a node20 release to the latest major version that
   declares `using: node24` in its own `action.yml` (verify this per-action, don't assume —
   check `https://raw.githubusercontent.com/<owner>/<repo>/<tag>/action.yml`). As of this
   writing, the versions confirmed node24 are:
   - `actions/checkout` → `v7`
   - `actions/setup-python` → `v7`
   - `actions/setup-node` → `v7`
   - `gitleaks/gitleaks-action` → `v3`
   - `astral-sh/setup-uv` → `v9` (only relevant to repos using `uv`, e.g. `octopus-monitoring`)
2. If the repo's self-hosted runner is affected (i.e. any of the above run on a self-hosted
   job), confirm the runner version is `>= v2.327.1` (minimum for Node 24) before bumping —
   check via `gh run view <a-recent-run-id> --log | grep "Current runner version"`. Every
   runner checked so far in this series (`hypervolt-agile-runner`, presumably
   `bin-reminder-runner`, `hueshift-runner`, `octopus-runner`) has been well above this
   threshold, but verify per-repo rather than assuming.
3. Replace `docker/login-action` and `docker/build-push-action` with plain `docker login` /
   `docker buildx build --push` shell commands wrapped in `nick-fields/retry@v4`
   (`max_attempts: 3`, `retry_wait_seconds: 15`, `timeout_minutes: 5` — `timeout_minutes` or
   `timeout_seconds` is required by this action despite being documented as optional). Neither
   original action supports native per-step retry, and `nick-fields/retry` only wraps raw
   shell `command:`, not other `uses:` actions — this is why the two actions get replaced
   rather than wrapped.
4. Building from `docker buildx build --push --tag "$TAG" .` (the local checkout) rather than
   `docker/build-push-action`'s previous default of a separate remote `git#{sha}` fetch is a
   deliberate, valuable side effect — the remote-fetch path was the confirmed root cause of one
   of two transient CI failures investigated in `hypervolt-agile`. Document this inline with a
   YAML comment above the `command:` block so a future maintainer doesn't "fix" it back to a
   remote-context build.
5. Pass Docker Hub credentials to the retry-wrapped `docker login` via the step's `env:` block
   (`DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`), referenced in the shell command as
   `$DOCKERHUB_TOKEN` etc. — not via `--password` on the command line (avoids the secret
   appearing in `ps` output; GitHub's log-masking covers both approaches equally, but
   `--password-stdin` is the safer habit).

## Per-Repo Adjustments

Not every repo has the same file layout — check before assuming:

- Some repos have no separate `ci-checks.yml`/quality-check workflow at all (confirmed for
  `bromley-bin-reminder`, `hueshift2` as of 2026-08-11) — only the Docker
  build/publish workflow needs touching.
- `octopus-monitoring` extracted its lint/type/security steps into a local composite action
  (`.github/actions/code-quality-checks/action.yml`) — the action pins inside that file need
  bumping too, not just the two top-level workflow files.
- `hueshift2`'s default branch is `master`, not `main` — the tag-selection logic
  (`if [ $branchName != 'main' ]`) uses whatever this repo's actual default branch is; check
  it rather than copying `main` verbatim.
- Docker image name (`DOCKER_IMAGE` env var) and workflow/job names differ per repo — read the
  existing file first, change only what this spec calls for.

## Testing Decisions

No application test suite is touched by this change; verification is direct execution, same
convention as the rest of this series:

- Push a commit on the working branch and confirm via `gh run watch` that both the Docker
  build/publish workflow (and the quality-check workflow, if one exists) succeed.
- Confirm via `gh run view <run-id> --log | grep -i deprecat` that no "Node.js 20 is
  deprecated" warning remains in either run's logs.
- Two-axis code review (Standards + Spec) to at least one clean pass; Copilot PR review
  requested manually (`gh pr edit <n> --add-reviewer '@copilot'`) since automatic Copilot
  review was disabled repo-wide during the security-hardening pass — confirm this is still
  true for the target repo before assuming a manual request is needed.

## Out of Scope

- Any repo whose security hardening (trigger fix, CODEOWNERS, ruleset) isn't done yet — do
  that first, as its own pass, before this one.
- `music-library-search` — re-audit needed before any work here; as of 2026-08-11 `main` has
  no Docker/CI workflow at all, contradicting the original cross-repo audit's claim of a
  dormant `ci-arm64.yml`. Don't assume this spec applies until that's resolved.
- Any further consolidation (e.g. extracting a shared composite action across repos) — each
  repo's fix stays local to that repo.

## Further Notes

- This is a small, well-scoped, low-risk fix. A direct implementation + two-axis review +
  Copilot review loop (skip the full worktree → grill → spec → issues ceremony) is
  proportionate — this spec exists so that ceremony can be skipped without losing the design
  reasoning.
