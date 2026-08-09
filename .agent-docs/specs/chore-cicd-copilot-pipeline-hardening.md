# CI/CD & Copilot Pipeline Hardening (hypervolt-agile)

## Problem Statement

`ci-arm64.yml` triggers on both `push` and `pull_request` against `main`, runs on the
self-hosted ARM64 runner (`hypervolt-agile-runner`), and unconditionally checks out PR head
code via `actions/checkout@v2` with no `ref:` override. Since this is a public repo, a
stranger's fork PR can execute arbitrary code on that runner today — a live exposure, not a
hypothetical one.

Separately, the existing `main-branch-protection` ruleset on `main`
(`required_approving_review_count: 0`, `require_code_owner_review: false`) permits
unreviewed merges, there is no CODEOWNERS file, and the ruleset's `copilot_code_review` rule
(`review_on_push: true`, `review_draft_pull_requests: true`) is spending Copilot review
credits on every push regardless of intent.

## Solution

1. Close the runner exposure: `ci-arm64.yml` drops its `pull_request` trigger entirely and
   becomes `push`-only. `ci-checks.yml` (hosted, already safe, but also triggers on both
   `push` and `pull_request`) does the same, eliminating the redundant second run it
   currently produces on same-repo PRs. Required status checks still attach correctly because
   GitHub matches checks to a PR's head commit SHA regardless of which event produced them —
   a `push` to a branch already covers that branch's own PR.

   This repo takes no responsibility for fork PR CI: there is no hosted fallback workflow.
   Anyone forking the repo gets zero automated checks on their PR until/unless the owner
   manually pushes their branch into this repo. This is a deliberate policy choice, not an
   oversight — recorded as an ADR since a future reader could otherwise mistake the absence
   of fork coverage for a gap.

2. Add `.github/CODEOWNERS` (`* @mholubinka1`) and edit the *existing*
   `main-branch-protection` ruleset (id `16484655`) to require code owner review
   (`require_code_owner_review: true`), matching the working configuration already proven on
   `octopus-monitoring` (ruleset id `20551010`): `required_approving_review_count` stays `0`.
   GitHub blocks self-approval, but a personal-repo owner still merges past their own ruleset
   with no formal "Approve" review recorded — confirmed by inspecting how `octopus-monitoring`
   PR #482 actually merged. No new ruleset is created.

3. Remove the `copilot_code_review` rule from that same ruleset, disabling automatic Copilot
   review on push while leaving on-demand/manual Copilot review untouched.

4. Disable `auto_request_review.yml` via the Actions API, then delete it and
   `.github/reviewers.yml` in this same PR — both are now redundant once CODEOWNERS is
   active, and `reviewers.yml` names only `mholubinka1` so nothing else needs replicating.
   Leave a follow-up note to verify CODEOWNERS auto-assigns the reviewer on the next real PR
   post-merge, since CODEOWNERS is evaluated from the base branch and can't be proven inside
   this PR itself.

5. Flag, but do not attempt, "Require approval for all outside collaborators" under
   Settings → Actions → General — there is no public GitHub REST API for this setting
   (confirmed during the `octopus-monitoring` work), so it stays a manual follow-up.

## User Stories

1. As the repo owner, I want the self-hosted ARM64 runner to never execute code from a PR
   opened by anyone but me, so a stranger can't run arbitrary code on my hardware via a fork
   PR.
2. As the repo owner, I want my own pushes and PRs to keep getting full CI coverage
   (lint/type/security/docker build), so closing the runner exposure costs me nothing.
3. As the repo owner, I don't want to spend any effort supporting fork PR CI — that's the
   forker's responsibility, not mine.
4. As the repo owner, I want every PR to require code owner review before merge, enforced
   structurally via a ruleset, without ever blocking my own solo merges.
5. As the repo owner, I want automatic Copilot code review off by default, so Copilot credit
   spend only happens when I explicitly request a review.
6. As the repo owner, I want the redundant custom reviewer-assignment workflow gone once
   CODEOWNERS covers the same job natively.

## Implementation Decisions

- **`ci-arm64.yml`**: `on:` becomes `push` only (`branches: ['**']`, unchanged). Runner label
  and all build/push steps unchanged — the docker build/push behaviour for push events is
  untouched; only the `pull_request` trigger path is removed.
- **`ci-checks.yml`**: `on:` becomes `push` only (`branches: ['**']`, unchanged). Runner stays
  `ubuntu-latest` — no self-hosted migration, since that was never in scope here (unlike the
  `octopus-monitoring` precedent, this repo's `ci-checks.yml` was never exploitable and moving
  it to self-hosted infrastructure is a separate cost/architecture decision, not part of this
  security pass). The checkout step's `ref: ${{ github.event.pull_request.head.sha ||
  github.sha }}` override is also removed — it's dead once `pull_request` is gone (the
  expression always resolved to `github.sha` on `push` events anyway), so this is inert
  cleanup, not a behavioural change.
- No `ci-fork-checks.yml`, no composite action — there is nothing to fall back to and nothing
  to deduplicate, since no second workflow exists.
- **`.github/CODEOWNERS`**: single line, `* @mholubinka1`.
- **Ruleset `main-branch-protection` (id `16484655`)** edit via `gh api`:
  - `pull_request` rule: `require_code_owner_review` → `true`. All other parameters on that
    rule (`required_approving_review_count: 0`, `dismiss_stale_reviews_on_push: false`,
    `required_reviewers: []`, `require_last_push_approval: false`,
    `required_review_thread_resolution: false`, `allowed_merge_methods`) unchanged.
  - `copilot_code_review` rule: removed from the ruleset's `rules` array entirely.
  - `deletion` and `non_fast_forward` rules: unchanged.
- **Delete** `.github/workflows/auto_request_review.yml` and `.github/reviewers.yml` in this
  PR, after disabling the workflow via `gh api repos/.../actions/workflows/.../disable`.
- **New ADR** in `.agent-docs/adr/` documenting: push-only triggers on both self-hosted-risk
  and hosted-redundant workflows, zero fork-PR CI by deliberate policy (not a fallback gap),
  and the ruleset self-approval precedent from `octopus-monitoring`.

## Testing Decisions

There is no application code under test here — verification is direct, per this repo's
existing "no tests, verify by execution" convention:

- After the trigger change: push a commit on this branch and confirm `ci-arm64.yml` and
  `ci-checks.yml` fire on `push` (`gh run watch`); confirm neither lists `pull_request` in
  its `on:` block anymore.
- After the ruleset edit: `gh api repos/mholubinka1/hypervolt-agile/rulesets/16484655` shows
  `require_code_owner_review: true` and no `copilot_code_review` rule present.
- After CODEOWNERS: confirmed present with correct content; reviewer auto-assignment verified
  on the next real PR post-merge (recorded as a follow-up, not verifiable pre-merge).
- After deleting `auto_request_review.yml`: confirm it no longer appears in
  `gh workflow list` (deleted, not just disabled).

## Out of Scope

- Migrating `ci-checks.yml` to self-hosted infrastructure (cost optimization, not a security
  fix — separate decision if ever pursued).
- Any fork-PR CI fallback workflow — explicitly rejected; forkers are responsible for their
  own CI.
- All other repos named in the wider cross-repo audit (`bromley-bin-reminder`, `hueshift2`,
  `music-library-search`, `learning-react`, `skills`) — each gets its own `/implement` pass.
- Dependabot scheduling changes, `actions_storage` migration, Copilot plan/budget changes.
- Manually setting "Require approval for all outside collaborators" — flagged for the user to
  do by hand (Settings → Actions → General); no API exists for it.

## Further Notes

- The final hardening summary (what changed, what's now safe, what remains a manual step)
  should be handed to the user at the end of this `/implement` run, per their explicit
  request.
- `.gitignore` had a pre-existing `CONTEXT.md` rule that, on this case-insensitive Windows
  checkout, was also shadowing the new `.agent-docs/context.md` domain glossary. Fixed by
  anchoring it to `/CONTEXT.md` (already committed, separate from this spec's scope).
