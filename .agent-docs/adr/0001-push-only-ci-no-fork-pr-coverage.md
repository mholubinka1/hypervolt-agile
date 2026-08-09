# Self-hosted and hosted CI trigger on push only; fork PRs get no automated checks

`ci-arm64.yml` was self-hosted (ARM64 runner `hypervolt-agile-runner`) and triggered on both
`push` and `pull_request`, unconditionally checking out and executing PR code via
`actions/checkout@v2` with no `ref:` override. Since this repo is public, a stranger's fork
PR could execute arbitrary code on that runner — a live exposure, not a hypothetical one.
`ci-checks.yml` (hosted, `ubuntu-latest`) had the same dual trigger, which wasn't a security
issue but did double-run on every same-repo PR, wasting hosted minutes.

We removed `pull_request` from both workflows entirely, leaving `push`-only. GitHub matches
required status checks to a PR's head commit SHA regardless of which event produced the check
run, so a `push` to a branch already satisfies that branch's own PR checks — same-repo PRs
lose no coverage.

Unlike the equivalent fix on `octopus-monitoring` (see that repo's ADR 0012), no hosted-only
fallback workflow was added for genuine fork PRs. This is a deliberate policy choice: the repo
owner takes no responsibility for fork PR CI. A fork PR gets zero automated checks — lint,
type-check, security scan, or Docker build — until the owner chooses to pull it into a branch
of this repo. The alternative (a `ci-fork-checks.yml` gated on `head.repo.full_name !=
github.repository`, as done on `octopus-monitoring`) was considered and rejected as
unnecessary complexity for a repo where outside contribution isn't a goal.

**Known residual gap, accepted rather than fixed**: `ci-arm64.yml`'s Docker build/push step is
still unconditional (`push: true`, `branches: ['**']`) — any push by a repo collaborator to any
branch publishes an image to Docker Hub, pre-existing behaviour this pass didn't touch. That's
a separate, non-fork-related concern from the exposure this ADR addresses (it requires push
access to this repo, which a fork PR alone never grants) and is left as a future cleanup if it
ever becomes a problem.

**Post-merge verification**: confirmed on a real PR against `main` after this change merged —
`ci-arm64.yml`/`ci-checks.yml` did not run on the `pull_request` event, `mholubinka1` was
auto-assigned as reviewer via CODEOWNERS, and required status checks from the branch's `push`
run attached correctly to the PR.
