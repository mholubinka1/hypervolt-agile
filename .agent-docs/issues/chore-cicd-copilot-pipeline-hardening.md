# Issues: chore-cicd-copilot-pipeline-hardening

> Work complete — PR ready to merge.

## Close the self-hosted runner exposure and drop redundant hosted PR runs (#59)

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

Remove the `pull_request` trigger from `ci-arm64.yml` (self-hosted ARM64 runner) so a fork
PR can never execute code there — leave the `push` trigger and all build/push steps
unchanged. Do the same for `ci-checks.yml` (hosted, already safe, but currently double-runs
on same-repo PRs). No fallback workflow is added for fork PRs — that's a deliberate policy
choice, not a gap, since this repo takes no responsibility for fork CI. Record the decision
as a new ADR in `.agent-docs/adr/`, covering: why `pull_request` was dropped from both
workflows, why there's deliberately no fork-PR fallback, and confirmation that required
status checks still attach correctly via SHA-matching on `push`.

### Acceptance criteria

- [x] `ci-arm64.yml`: `on:` is `push` only; runner label and build/push steps unchanged
- [x] `ci-checks.yml`: `on:` is `push` only; runner (`ubuntu-latest`) and all steps unchanged
- [x] No new fork-PR fallback workflow exists
- [x] New ADR added to `.agent-docs/adr/` documenting the push-only + no-fork-coverage
      decision
- [x] A real push on this branch triggers both workflows successfully (`gh run watch`)

---

## Add CODEOWNERS and require Code Owner review (#60)

**Blocked by**: None

**User stories**: 4

### What to build

Add `.github/CODEOWNERS` assigning every path to `@mholubinka1`. Edit the existing
`main-branch-protection` ruleset (id `16484655`) on `main` — do not create a new ruleset —
setting `require_code_owner_review: true` on its `pull_request` rule.
`required_approving_review_count` stays `0`, matching the proven working configuration on
`octopus-monitoring` (ruleset id `20551010`), where the repo owner still merges past the
ruleset on a personal repo despite GitHub blocking self-approval.

### Acceptance criteria

- [x] `.github/CODEOWNERS` contains `* @mholubinka1`
- [x] Ruleset `16484655`'s `pull_request` rule has `require_code_owner_review: true`,
      `required_approving_review_count: 0`, all other parameters unchanged (verified via
      `gh api repos/mholubinka1/hypervolt-agile/rulesets/16484655`)

---

## Disable automatic Copilot code review (#61)

**Blocked by**: None

**User stories**: 5

### What to build

Remove the `copilot_code_review` rule from the `main-branch-protection` ruleset entirely,
leaving on-demand/manual Copilot review untouched.

### Acceptance criteria

- [x] Ruleset `16484655`'s `rules` array no longer contains a `copilot_code_review` entry
      (verified via `gh api`)

---

## Remove auto_request_review.yml and reviewers.yml (#62)

**Blocked by**: #60 (Add CODEOWNERS and require Code Owner review)

**User stories**: 6

### What to build

Disable the `auto_request_review.yml` workflow via the Actions API, then delete it and
`.github/reviewers.yml` in this same PR — both are redundant once CODEOWNERS is active, and
`reviewers.yml` names only `mholubinka1` so nothing else needs replicating. Record a
follow-up note for the user to confirm CODEOWNERS auto-assigns the reviewer on the next real
PR post-merge, since this can't be proven from inside this PR.

### Acceptance criteria

- [x] `auto_request_review.yml` disabled via `gh api` before deletion
- [x] `.github/workflows/auto_request_review.yml` deleted
- [x] `.github/reviewers.yml` deleted
- [x] Follow-up note recorded: after this PR merges, confirm `mholubinka1` is auto-assigned
      via CODEOWNERS on the next real PR

---
