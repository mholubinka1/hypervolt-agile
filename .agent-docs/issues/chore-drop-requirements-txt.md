# Issues: chore-drop-requirements-txt

## Make uv.lock the single dependency lockfile of record — [#155](https://github.com/mholubinka1/hypervolt-agile/issues/155)

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5

### What to build

Remove the committed `requirements.txt` and the `uv-export` pre-commit hook that keeps
regenerating it, so `uv.lock` is the only lockfile in version control. Point Dependabot
cleanly at `uv.lock` by removing the `requirements.txt` its pip updater was latching onto,
and harden its config (weekly schedule, 7-day cooldown). Keep the `uv-lock` hook. Nothing in
the repo consumes `requirements.txt` — Docker and CI install from `uv.lock` via
`uv sync --frozen`.

### Acceptance criteria

- [ ] `uvx pre-commit run --all-files` passes every hook; `.pre-commit-config.yaml` no longer
      contains a `uv-export` hook, and the `uv-lock` hook is still present under the
      `astral-sh/uv-pre-commit` repo
- [ ] `git ls-files` does not list `requirements.txt`; the file is absent from the working
      tree; `.gitignore` contains a `requirements.txt` entry with a comment noting it is a
      derived `uv export` artifact
- [ ] `uv lock --check` succeeds (lockfile still consistent with `pyproject.toml`) and
      `uv sync --frozen` succeeds (frozen install still resolves)
- [ ] `uv run pytest -q` reports 292 passing — the suite is unaffected
- [ ] A grep for `requirements.txt` across `.github/`, `Dockerfile`, `scripts/`,
      `.dockerignore` and `README.md` returns only the new `.gitignore` /
      `.pre-commit-config.yaml` comment and pre-existing historical `.agent-docs/` specs — no
      build, CI, or runtime consumer
- [ ] `.github/dependabot.yml`: `schedule.interval` is `weekly`; a `cooldown` block with
      `default-days: 7` is present on the `uv` update entry; `package-ecosystem: uv`,
      `directory: "/"` and the `python-packages` group are unchanged; the file is valid
      Dependabot v2 YAML
- [ ] `.pre-commit-config.yaml` carries a comment where the `uv-export` hook was: it was
      removed, and `uv export --frozen -o requirements.txt` produces the file on demand
- [ ] (Post-merge, documented in the spec, not gated by this issue) within ~1 week Dependabot
      opens a PR that updates `uv.lock`, or its run reports success with nothing to update;
      otherwise the Dependabot run log is inspected and `/update-dependencies` covers the gap

---
