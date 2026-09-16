# Issues: chore-behaviour-config-not-mutated

## Give the threshold extension its cadence as a constructor parameter, not an injected config key

**GitHub issue**: #178

**Blocked by**: None (branch is based on PR #175's branch, which already contains this function)

**User stories**: 1, 2

### What to build

Extend `common/extensions.py`'s `load_extensions` with an optional `extra_kwargs: dict[str, Any] |
None = None` parameter, passed as `**extra_kwargs` to the provider constructor alongside
`entry.config`. Defaults to nothing extra, so `hypervolt/led.py`'s own `load_extensions` wrapper is
unaffected.

Change `app/schedule/behaviour.py`'s `load_threshold_extension` to stop copying and rewriting
`entry.config` — pass `entry` straight through untouched, and pass `extra_kwargs={"update_every_mins":
update_every_mins}` instead. Change `BehaviourProvider`'s `__init__` signature (same file) to accept
`update_every_mins: int` as a second parameter. Change
`extensions/behaviours/dynamic_charging_threshold.py`'s `DynamicChargingThresholdExtension.__init__`
to accept it as a real constructor argument instead of reading it out of `config`.

### Acceptance criteria

- [ ] `load_extensions` accepts an optional `extra_kwargs` dict and passes it to the provider
      constructor as keyword arguments; omitting it (or passing `None`) behaves exactly as today for
      every existing caller.
- [ ] `load_threshold_extension` no longer calls `entry.model_copy(...)` — the `ExtensionEntry`
      (and its `config` dict) it hands to the shared loader is the exact object the caller passed in.
- [ ] `DynamicChargingThresholdExtension` receives its poll cadence via a constructor parameter, not
      by reading `config["update_every_mins"]`.
- [ ] An operator who writes `update_every_mins` inside the threshold extension's own config block
      sees that value passed through to the extension's `config` dict untouched (available via
      `config.get("update_every_mins")` if the extension chooses to look), not overwritten with the
      loader-injected value — the two can now legitimately differ, proving the config is genuinely no
      longer rewritten.
- [ ] Every existing test covering cadence injection, config loading, and the threshold extension's
      own behaviour still passes, updated to the new constructor-parameter call shape rather than the
      old config-key shape.

---
