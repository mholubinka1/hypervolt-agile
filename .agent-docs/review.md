# Review Criteria (repo-specific)

Review criteria this repository has accumulated from its own Copilot review rounds.

- The `address-copilot-comments` skill appends a generalised, one-line criterion here for
  every Copilot finding on a PR that resulted in a code change. Findings that were pushed
  back on ("Ignored.") are never recorded — this file only holds criteria the team accepted
  by changing code.
- The `code-review` skill feeds this file to its Standards sub-agent alongside the skill's
  own `REVIEW-CRITERIA.md`, and treats the entries here as documented repo standards (a
  breach may be blocking).
- Prune stale entries, and promote durable ones into a shared criteria file, by hand.

Each entry is a bold label plus a one-line imperative rule, tagged with the PR it came
from — for example:
`- **Partial checks for compound state**: flag a readiness check that inspects one artefact when the state it gates has several parts. (PR #58)`

## Criteria

- **Unguarded cleanup sequences**: flag a teardown or failure-path cleanup that calls multiple release steps back-to-back with no individual guard — one step raising skips the rest and leaks whatever it would have released. (PR #100)
- **Non-native interactive elements without keyboard support**: flag a click handler attached to an element that isn't natively focusable (e.g. an SVG shape) with no `tabindex`, role, and keydown handling for Enter/Space alongside it. (PR #100)
- **Inputs without a real accessible name**: flag a text input whose only visible identifier is a placeholder or an unassociated nearby label — use a proper `<label for>` or `aria-label` instead. (PR #100)
- **Inconsistent lockfile-strict installs**: flag a dependency-install command that omits the lockfile-strict flag (e.g. `--frozen`) when another install path for the same lockfile in the repo already uses it — every install of the same lockfile should fail fast on drift the same way, not just some of them. (PR #104)
