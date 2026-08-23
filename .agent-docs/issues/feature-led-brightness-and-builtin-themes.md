# Issues: feature-led-brightness-and-builtin-themes

## Push LED brightness while charging (#75)

**Blocked by**: None

**User stories**: 1, 3

### What to build

Full path for brightness control, with no effects yet: a `led:` block in `config.yml`
(`LedConfig(enabled: bool = True, brightness: float = Field(0.5, gt=0, le=1))` on `AppConfig` —
**brightness added 2026-08-23**, defaults to 50% when omitted), a `set_led_brightness` method on
`HypervoltWebSocketClient` (matching `set_lock_state`'s pattern — no `HypervoltProtocol` change
needed, see spec), a new `apply_led_state(brightness,
effect_name)` method on `HypervoltChargerClient` that diffs against the charger's echoed
`led_brightness` before pushing (mirroring `apply_schedule`'s diffing pattern exactly), and
`ScheduleCoordinator._apply_led_state()` called unconditionally from `run()` after `refresh()` —
gated solely on `led.enabled` and `charger_state.is_charging`, never on `_can_push()`, since LED
state reflects the charger's actual physical state regardless of who is driving it.
`effect_name` is always passed as `None` in this issue; the parameter exists so the next issue
extends this method rather than reworking it.

### Acceptance criteria

- [ ] Given `led` is enabled, `is_charging` is `True`, and `led_brightness` is not the configured
      brightness, when the scheduler runs, then the configured brightness is sent to the charger
- [ ] Given `led` is enabled with no `brightness` key set, when the scheduler runs while charging,
      then `0.5` is sent (default)
- [ ] Given `led.brightness: 0.8` is set, when the scheduler runs while charging, then `0.8` is
      sent, not `0.5`
- [ ] Given `led` is enabled, `is_charging` is `False`, and `led_brightness` is not `0.0`, when
      the scheduler runs, then brightness `0.0` is sent to the charger (never the configured
      value — the off state is not configurable)
- [ ] Given `is_charging` is `True` and `led_brightness` already matches the configured
      brightness, when the scheduler runs, then no LED message is sent
- [ ] Given no `led:` block exists in `config.yml`, when the scheduler runs, then no LED messages
      are ever sent
- [ ] Given `led.enabled` is `false`, when the scheduler runs, then no LED messages are sent, and
      whatever brightness was last commanded stays as-is (no reset push)
- [ ] `config/config.yml.template` documents the `led:` block with `enabled` and `brightness`
- [ ] LED control runs every cycle regardless of `_can_push()` (verified by forcing a released /
      not-pushable state while `is_charging` is `True` and confirming brightness is still sent)

---

## Apply built-in seasonal effects while charging (#76)

**Blocked by**: #75 (Push LED brightness while charging)

**User stories**: 2

### What to build

New `app/hypervolt/led.py` module: a `LedTheme` dataclass (`effect_name: str`), the hardcoded
`BUILT_IN_THEMES` list (`halloween_mode` 31 Oct 00:00 → 1 Nov 06:00; `christmas_mode` 24 Dec
00:00 → 31 Dec 06:00; `party_mode` 31 Dec 06:00 → 1 Jan 06:00, all year-agnostic London local
time), and `resolve_theme(now, extensions=(), custom_themes=()) -> Optional[LedTheme]` walking
the three-tier priority stack — only the built-in tier is populated this issue, but the
`extensions`/`custom_themes` parameters exist now so later work extends this signature rather
than reworking it. Add `set_led_effect` to `HypervoltWebSocketClient` (same pattern as
`set_led_brightness`), and extend `apply_led_state` (from #1) to also diff/push `effect_name`
against a new
`_current_led_effect` field. `ScheduleCoordinator` resolves the target theme each cycle via
`resolve_theme(now)` and passes its `effect_name` through.

### Acceptance criteria

- [ ] Given the local datetime is 31 Oct (any time) through 1 Nov 06:00 and `is_charging` is
      `True`, when the scheduler runs, then `effect_name` `halloween_mode` is sent
- [ ] Given the local datetime is between 24 Dec 00:00 and 31 Dec 06:00 and `is_charging` is
      `True`, then `effect_name` `christmas_mode` is sent
- [ ] Given the local datetime is between 31 Dec 06:00 and 1 Jan 06:00 and `is_charging` is
      `True`, then `effect_name` `party_mode` is sent
- [ ] Given the local datetime falls within no theme window and `is_charging` is `True`, then
      the configured brightness is sent if needed and no `effect_name` is sent
- [ ] Given `halloween_mode` is active mid-charge, when the local datetime passes 1 Nov 06:00,
      then no effect is sent on the next cycle and `_current_led_effect` is cleared
- [ ] No redundant effect push when `_current_led_effect` already matches the resolved theme

---
