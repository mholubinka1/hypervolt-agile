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
- **Unchecked numeric coercion of external input**: flag `Number(...)` applied to a DOM attribute or user-entered value that is then stored or JSON-serialised without a `Number.isFinite` guard — `JSON.stringify(NaN)` is `null`, silently indistinguishable from a genuinely absent value. (PR #108)
- **Inconsistent fail-fast on element lookups**: flag a `querySelector`/`getElementById` result dereferenced with no presence check when the same file already throws a clear diagnostic for a similar lookup — guard every such lookup the same way. (PR #108)
- **Prose contradicting a restated concrete value**: flag a dimension, ordering, or enumeration stated one way in prose when the code (or elsewhere in the same document) states it the other way. (PR #108)
- **Prototype-chain reachable keys from parsed input**: flag a loop over `Object.keys()` of an externally-parsed object that gates each write with a truthiness check (`if (target[k])`) — a key like `__proto__` passes through the prototype chain and the write pollutes the prototype. Gate on `Object.prototype.hasOwnProperty.call(target, k)` (or an explicit allowed-key set) instead. (PR #113)
- **Ambiguous log line across multiplexed code paths**: flag a warning/error/recovery log emitted from a helper or dispatch point that now serves more than one operation or entry point (e.g. one method handling several provider hooks) when the message names neither the operation nor a discriminator — a reader cannot tell which path produced it, especially when one path keeps failing while another succeeds. Include the operation/method name in the message. (PR #118)
- **Linear-scan lookup in a sort-key or repeated-lookup path**: flag a `.index()` call on a list or tuple used as a sort key or evaluated once per comparison/iteration — precompute a dict mapping (or equivalent O(1) structure) once instead of scanning on every call. (PR #138)
- **Stale spec/issue text after implementation deviates from plan**: flag a spec or issue file that still states a file name, scope claim ("no other code references X"), or other concrete fact contradicted by the final code — update the doc to match what was actually built rather than leaving it silently wrong. (PR #138)
- **Incomplete transaction/request outcome handling**: flag a promise wrapper around a transaction or request API that settles on only some of its outcome events (e.g. IndexedDB `oncomplete`/`onerror` but not `onabort`) — an unhandled outcome can leave the promise (and anything gating on it) hanging forever. Handle every outcome event through the same settle path, guarded so it can only fire once. (PR #141)
- **Unverified picker/dialog result cached under an expected key**: flag code that caches whatever a native picker or dialog returns (a file handle, a chosen path) under the key it *expected*, without confirming the result actually matches that key — the user can navigate to or type a different target, and every later operation then silently uses the wrong resource. (PR #141)
- **Stream left open on a mid-write failure**: flag a writable/output stream whose `write()`/`close()` failure path doesn't abort the stream before propagating the error — an open stream can hold a lock or leave a pending write that makes the next attempt unreliable. Abort (best-effort) before rethrowing. (PR #141)
