# uv.lock is the single dependency lockfile of record

## Problem Statement

`pre-commit run --all-files` fails on a routine basis. The failing hook is `uv-export`,
which regenerates the committed `requirements.txt` from `uv.lock` — and `requirements.txt`
keeps drifting out from under it. Every time a maintainer hits this they have to stop and
re-sync `requirements.txt` by hand (commit `c77406e`, then PR #154) before they can commit
anything at all.

The drift is coming from Dependabot. `.github/dependabot.yml` has declared
`package-ecosystem: uv` since the Poetry→uv migration (#102), but every Dependabot update
commit since then has edited `requirements.txt` alone and never touched `uv.lock` — and it
labels transitive packages like `anyio` (pulled in by `httpx`, not named in
`pyproject.toml`) as `direct:production`. That is pip-updater behaviour: Dependabot is
maintaining the flat hashed `requirements.txt` as if it were the manifest, while the rest of
the project treats `uv.lock` as authoritative. The two are never reconciled, so the hook
and Dependabot pull the file in opposite directions forever.

Nothing actually consumes `requirements.txt`. The Dockerfile installs with
`uv sync --frozen --no-dev`; CI installs with `uv sync --frozen` and guards the lockfile
with `uv lock --check`; the ARM64 workflow just builds the Docker context. No script,
workflow, or `.dockerignore` entry references it; the README only mentions
`pip install pre-commit`. It is a leftover Poetry-era export.

## Solution

Make `uv.lock` the one lockfile the repository keeps, and stop producing the export that
fights Dependabot:

- `requirements.txt` is deleted and git-ignored — a derived artifact, not source. Anyone who
  needs a pip-style file runs `uv export --frozen -o requirements.txt` on demand.
- The `uv-export` pre-commit hook is removed. The `uv-lock` hook stays, so `uv.lock` is
  still verified against `pyproject.toml` on every commit.
- `.github/dependabot.yml` is pointed cleanly at `uv.lock`: with no `requirements.txt` in
  the tree for the pip updater to latch onto, Dependabot's `uv` ecosystem has only
  `pyproject.toml` + `uv.lock` to act on. A `cooldown` is added (per Astral's guidance) so
  Dependabot doesn't open PRs for versions uv can't yet resolve, and the schedule is relaxed
  from daily to weekly to cut PR noise on a grouped-update repo.

After this, `pre-commit run --all-files` stays green with no manual intervention, and
dependency updates flow through the same `uv.lock` that Docker and CI already use.

## User Stories

1. As a maintainer, I want `pre-commit run --all-files` to pass without first hand-syncing a
   generated file, so that committing unrelated work is never blocked by dependency
   bookkeeping.
2. As a maintainer, I want exactly one lockfile in version control, so that there is no
   question of which file is authoritative or any way for two of them to disagree.
3. As a maintainer, I want Dependabot to update `uv.lock` — the file the Docker image and CI
   install from — so that an automated dependency bump is actually reflected in what ships.
4. As a maintainer, I want a low-noise, resolvable stream of Dependabot PRs, so that I
   review a weekly batch rather than a daily trickle, and never one uv can't lock.
5. As a contributor who still wants a `requirements.txt`, I want an obvious one-line command
   to generate it, so that dropping it from the repo costs me nothing.

## Implementation Decisions

- **`requirements.txt`**: removed from git and from the working tree (`git rm`). Added to
  `.gitignore` with a comment noting it is a derived `uv export` artifact.
- **`.pre-commit-config.yaml`**: the `uv-export` hook entry and its multi-line explanatory
  comment are deleted. The `uv-lock` hook under the same `astral-sh/uv-pre-commit` repo
  stays. A short replacement comment records that the export hook was removed and that
  `uv export --frozen -o requirements.txt` produces the file on demand.
- **`.github/dependabot.yml`**: `schedule.interval` changes from `daily` to `weekly`; a
  `cooldown:` block with `default-days: 7` is added to the `uv` update entry.
  `package-ecosystem: uv`, `directory: "/"`, and `groups.python-packages.patterns: ["*"]`
  are left exactly as they are — they are already the recommended shape.
- **No ADR**: this spec and the PR description carry the rationale; the decision is cheap to
  reverse (re-add the hook and the file) if `uv.lock` Dependabot support turns out not to
  work for this repo.
- **No `context.md` change**: no new domain vocabulary.

## Testing Decisions

This slice changes only configuration and a generated file — there is no executable logic to
unit-test, and a test asserting "`requirements.txt` is untracked" or "the config has no
`uv-export` hook" would only pin the implementation. No automated test is added, consistent
with how the Poetry→uv migration slice (`chore-poetry-to-uv-migration.md`) was handled.

Verification is by running the toolchain the change touches:

- `uvx pre-commit run --all-files` — every hook green; `uv-lock` still runs, `uv-export` is
  gone.
- `uv lock --check` — `uv.lock` still consistent with `pyproject.toml`.
- `uv sync --frozen` — a frozen install still resolves (the path Docker and CI use).
- `uv run pytest -q` — the suite is unaffected (292 passing).
- A grep for `requirements.txt` across workflows, `Dockerfile`, `scripts/`, `.dockerignore`
  and `README.md` — confirms nothing else reads it.
- `.github/dependabot.yml` is valid YAML for Dependabot's v2 schema.

The coverage ratchet (ADR 0002) is untouched — no `app/` or `extensions/` code changes.

**Post-merge acceptance signal** (cannot be observed before merge): within roughly a week,
Dependabot opens a PR that updates `uv.lock`, **or** the repository's Dependabot run
(Insights → Dependency graph → Dependabot) shows a successful `uv` run with nothing to
update. If Dependabot instead goes silent or errors, the follow-up is to inspect that run
log and, in the meantime, keep dependencies current with the `/update-dependencies` skill.

## Out of Scope

- Any dependency version change — `uv.lock` and `pyproject.toml` versions are untouched.
- Changes to the `Dockerfile` or to either CI workflow.
- Changing Dependabot's ecosystem name or the `groups` block.
- Rewriting the historical specs that mention the old `uv-export` hook
  (`chore-poetry-to-uv-migration.md`, `chore-themes-and-charger-led-map.md`) — they remain
  accurate accounts of the work done at the time.

## Further Notes

- Astral's own Dependabot guide (`docs.astral.sh/uv/guides/integration/dependabot/`)
  gives `package-ecosystem: "uv"` + `cooldown` as the recommended configuration and notes
  that "some use cases are not yet working", pointing at a dependabot-core tracking issue —
  hence the post-merge check above rather than an assumption that it Just Works.
- GitHub's dependency graph and Dependabot **alerts** read `uv.lock` natively, so deleting
  `requirements.txt` does not reduce vulnerability scanning coverage.
