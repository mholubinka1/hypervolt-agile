# Dependency tooling moves from Poetry to uv

Every Python dependency surface — `pyproject.toml`, the lockfile, pre-commit hooks, the Docker
build, CI, and Dependabot — moves from Poetry to [uv](https://docs.astral.sh/uv/). The stated
reasons: uv is substantially faster (a single Rust binary, no separate interpreter/plugin install
step), and GitHub's own tooling has moved with it — Dependabot and the Dependency Graph both
gained native `uv.lock` support (GitHub changelog, 2026-04-23), where Poetry has always needed
either a plugin (`poetry-plugin-export`) or degraded dependency-graph coverage.

The alternative — staying on Poetry — was a real option, not a strawman: Poetry was already
working, is more mature, and has a larger plugin ecosystem. The migration is a genuine
cross-cutting change (CI workflows, the Docker multi-stage build, every pre-commit hook that
touches the lockfile, Dependabot's ecosystem config, and daily `poetry run` → `uv run` muscle
memory all change at once), not a drop-in swap — worth recording so a future reader doesn't
wonder why a working toolchain was replaced, and doesn't assume the choice is easily reversible
without redoing all of the above.
