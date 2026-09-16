# Stop behaviour.py from mutating the operator's extension config

## Problem Statement

`load_threshold_extension` (`app/schedule/behaviour.py`) needs to give the threshold extension the
scheduler's own poll cadence (`update_every_mins`) so the extension's polling interval never drifts
out of sync with the schedule's own update frequency (ADR: reusing the schedule's cadence "avoids a
redundant config field"). Today it does this by copying the operator's `ExtensionEntry.config` dict
and overwriting whatever the operator wrote under that same key: `entry.model_copy(update={"config":
{**entry.config, "update_every_mins": update_every_mins}})`. If an operator ever writes
`update_every_mins` in the extension's own YAML config block — a reasonable thing to try, since
every other tunable in that block lives there — it's silently discarded and replaced with a
different value, with no error and no log line. The config the extension actually receives is no
longer exactly what the operator wrote; a different module (`behaviour.py`) has reached into a
config shape owned by `AppConfig` and rewritten part of it.

## Solution

Give the threshold extension its cadence as a genuinely separate constructor parameter instead of
smuggling it into the config dict. This means extending the shared extension loader
(`common/extensions.py`, used by both the LED and threshold provider kinds) with an opt-in way to
pass extra constructor arguments to a loaded provider, so `behaviour.py` can hand `update_every_mins`
to the threshold extension directly rather than writing it into `entry.config` at all. The LED
provider kind passes nothing extra and is completely unaffected.

## User Stories

1. As an operator writing a threshold extension's config block, I want every key I write there to
   reach the extension exactly as I wrote it, so that nothing outside my own config file silently
   changes what I configured.
2. As a developer maintaining the extension-loading machinery, I want a documented, reusable way for
   a provider kind's own loader to inject a value the extension needs but the operator doesn't
   configure directly, so that a future provider kind needing the same shape doesn't have to invent
   its own dict-mutation trick.

## Implementation Decisions

- **`common/extensions.py`**: `load_extensions` gains an optional `extra_kwargs: dict[str, Any] |
  None = None` parameter, passed as `**extra_kwargs` to the provider class's constructor alongside
  `entry.config` (i.e. `_provider_class(entry.config, **(extra_kwargs or {}))`). Defaulting to `None`
  (treated as `{}`) means every existing call site — including `hypervolt/led.py`'s own
  `load_extensions` wrapper — is unaffected; LED passes no `extra_kwargs` and its call is unchanged.
- **`app/schedule/behaviour.py`**: `load_threshold_extension` no longer calls `entry.model_copy(...)`
  at all. It passes `entry` straight through to `_load_extensions` untouched, and passes
  `extra_kwargs={"update_every_mins": update_every_mins}` instead. `entry.config` reaching the
  extension is now exactly what the operator wrote, with no injected or overwritten key.
- **`BehaviourProvider` Protocol** (same file): its `__init__` signature changes from
  `__init__(self, config: dict[str, Any]) -> None` to `__init__(self, config: dict[str, Any],
  update_every_mins: int) -> None`. This is specific to the threshold kind's own contract, not a
  change to `LedThemeProvider` or any other kind's protocol — `BehaviourProvider` already only
  describes what the threshold kind's providers must implement (ADR 0021), so this stays a
  same-kind, same-file change.
- **`extensions/behaviours/dynamic_charging_threshold.py`**: `DynamicChargingThresholdExtension.__init__`
  gains `update_every_mins: int` as a second constructor parameter and assigns it directly
  (`self._update_every_mins = update_every_mins`), removing the `self._require_number(config,
  "update_every_mins")` line entirely. This value now arrives pre-validated (`AppConfig.schedule.frequency`
  is a Pydantic `int` field with `gt=0, le=1440`), so re-validating it as untrusted YAML input inside
  the extension is no longer meaningful — it's a trusted argument from the loader, not a value read
  out of the operator's own free-form config dict. If an operator's config block still happens to
  contain an `update_every_mins` key (a leftover from before this change, or a well-intentioned
  guess), it's simply never read — no error, no silent overwrite either, since nothing touches that
  key anymore.

## Testing Decisions

- `tests/common/test_extensions.py`: add a test proving `extra_kwargs` reaches the provider's
  constructor (a fake provider class recording what it was constructed with), and a test proving
  omitting `extra_kwargs` (or passing `None`) doesn't break construction for a provider whose
  constructor takes only `config` — covering both the new parameter and the unaffected default path.
- `tests/schedule/test_behaviour.py`: update the existing "injects the schedule's own cadence" test
  (currently named around asserting `update_every_mins` inside `config`) to prove cadence arrives via
  the new constructor parameter instead — the fake extension module written to `tmp_path` for this
  test needs its `__init__` signature updated to accept `update_every_mins` and expose it via
  `get_threshold()`, same as today's convention. Add a new test proving an operator-supplied
  `update_every_mins` key inside the extension's own config block is passed through to the extension
  untouched (not overwritten), by having the fake extension read `config.get("update_every_mins")`
  itself (distinct from the constructor's own `update_every_mins` argument) and asserting the two can
  differ — proving the config dict is genuinely no longer touched by the loader.
- `tests/extensions/behaviours/test_dynamic_charging_threshold.py`: every test constructing
  `DynamicChargingThresholdExtension` needs the call site updated to pass `update_every_mins` as a
  second argument instead of a `"update_every_mins"` key in the config dict passed to `_valid_config`.
  Read the file's existing `_valid_config` helper and every call site before changing it — this
  touches most of the file's tests, so get the exact call-site shape right once rather than piecemeal.

## Out of Scope

- Any change to `LedThemeProvider`, `hypervolt/led.py`'s own `load_extensions` wrapper, or LED's own
  config shape — `extra_kwargs` is opt-in and LED doesn't use it.
- Any change to how `update_every_mins` itself is computed or validated at the `AppConfig` level
  (`Schedule.frequency`) — it's already validated there; this only changes how it reaches the
  extension afterward.
- The other architecture-review candidates worked separately: #1/#2/#4 (PR #175, already merged or
  in review — this branch is based on it since it modifies the same `load_threshold_extension`
  function #173 introduced), #3 (PR #177), and #6 (config.py↔led.py import cycle).

## Further Notes

Source: architecture-review report generated 2026-09-15, candidate #5. This branch
(`chore/behaviour-config-not-mutated`) is based on `chore/consolidate-effective-limit-policy` (PR
#175) rather than `main`, since it modifies the exact `load_threshold_extension` function and
`_ThresholdValidatingProvider` wiring PR #175 introduced — working from `main` would create an
avoidable merge conflict in the same function once #175 lands.
