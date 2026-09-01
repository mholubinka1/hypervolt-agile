# Issues: chore-poetry-to-uv-migration

> Work complete — PR ready to merge.

## Migrate Poetry to uv, versions unchanged (#102)

**Blocked by**: None

**User stories**: 1, 3, 4

### What to build

Convert `pyproject.toml` from `[tool.poetry]` to PEP 621 (`[project]` + `[dependency-groups]` +
`[tool.uv] package = false`), with every dependency version translated to its explicit PEP 440
equivalent (no version changes yet) and the Python floor raised to `>=3.13,<4.0` to match what
Docker/CI actually run (applied consistently to `[tool.mypy]`'s `python_version` too). Generate
`uv.lock`. Swap the Poetry pre-commit hooks for `astral-sh/uv-pre-commit`'s `uv-lock`/`uv-export`.
Update the Dockerfile's builder stage to install uv and run `uv sync` instead of Poetry, landing
the interpreter at the same `.venv/bin/python` path the final stage already copies. Update
`ci-checks.yml` to use `astral-sh/setup-uv`, `uv run`, and `uv lock --check`, and bump its
`setup-python` version to 3.13. Flip `dependabot.yml`'s `package-ecosystem` from `pip` to `uv`.
Update README's install/run instructions from `poetry install`/`poetry run` to `uv sync`/`uv run`.

### Acceptance criteria

- [x] Given the converted `pyproject.toml`, when `uv lock` is run, then it succeeds and `uv.lock` resolves the same effective dependency versions `poetry.lock` had
- [x] Given the converted config, when `uv run pytest tests/` is run, then all 197 existing tests pass unchanged
- [x] Given the new `uv-lock`/`uv-export` pre-commit hooks, when `uv run pre-commit run --all-files` is run, then every hook passes
- [x] Given the updated Dockerfile, when `docker build .` is run locally, then it succeeds and the resulting image's `.venv/bin/python` still runs the app entrypoint correctly
- [x] Given the updated `ci-checks.yml`, when the branch is pushed, then the workflow goes green on GitHub Actions
- [x] Given README's updated commands, when followed from a clean checkout, then `uv sync` then `uv run python app/main.py --config-file config/config.yml` behaves the same as the old Poetry commands did

---

## Update dependencies to latest via uv (#103)

**Blocked by**: #102 (Migrate Poetry to uv, versions unchanged)

**User stories**: 2

### What to build

Once the migration slice is merged and green, run `uv lock --upgrade` to bring every runtime and
dev-group dependency to the latest version satisfying its `pyproject.toml` constraint, and `uv
sync` to reflect that locally. Bump every pinned pre-commit hook revision in
`.pre-commit-config.yaml` (mypy, isort, black, ruff, pre-commit-hooks, markdown-link-check,
gitleaks, bandit, and the newly-added `uv-pre-commit` entry) to its current latest tag. Report,
rather than silently absorb, any dependency whose latest available release exceeds what its
existing constraint in `pyproject.toml` allows (a genuine major bump).

### Acceptance criteria

- [x] Given the migration slice is complete, when `uv lock --upgrade` is run, then it succeeds and `uv.lock` reflects the latest version satisfying each constraint
- [x] Given the upgraded lock, when `uv run pytest tests/` is run, then all tests still pass, or any failure is triaged and reported rather than ignored
- [x] Given each pinned pre-commit hook, when checked against its repo's latest tag, then `.pre-commit-config.yaml` is updated to match
- [x] Given a dependency whose latest release exceeds its current `pyproject.toml` constraint, when found, then it is reported to the user rather than silently forced past the declared constraint

---
