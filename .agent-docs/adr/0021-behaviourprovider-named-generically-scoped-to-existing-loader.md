# The dynamic charging-threshold extension is named `BehaviourProvider`, the first kind to actually build ADR 0017's generalised loader — not a redesign of the extension mechanism

The dynamic charging-price-threshold feature (PHEV-only: compute the charging threshold from local
fuel price and MPG rather than a static config value) needs an extension-loaded provider — the
same shape ADR 0017 already decided the loader should support (`LedThemeProvider` today, the
planned `VehicleProvider` for the still-unbuilt Volvo spec). ADR 0017 recorded that decision but
nothing has built it yet: `load_extensions`, `_load_provider_class`, and `ExtensionWrapper` in
`app/hypervolt/led.py` today are still LED-specific (hardcoded to `hasattr(cls, "resolve")` and
`.resolve()`/`.resolve_fallback()`). This feature is what actually performs that extraction — into
a shared, marker-method-parameterised home — since it's the first non-LED provider kind to actually
get built, ahead of Volvo. The obvious name for the new protocol would be `ThresholdProvider`,
scoped narrowly to this one feature.

Decision: name the protocol `BehaviourProvider` instead — still just ADR 0017's
"one marker method identifies a valid provider class" shape (`get_threshold()` is this kind's
marker), loaded through the same generalised `extensions/` directory and shared loader this
feature extracts, with no behavioural change to `LedThemeProvider` and no change to the still-only
-planned `VehicleProvider`. The generic name signals this loader is meant to keep growing new
provider kinds as the app needs new pluggable operator behaviour, so a future kind isn't stuck
picking between an awkwardly-specific name or another naming exception. This is *not* a move to a
single capability-based protocol that LED/Vehicle/Threshold would all implement — that redesign
was explicitly considered and rejected as out of scope for this feature.

This is hard to reverse once a third provider kind and operator-facing extensions exist under this
name, and it's surprising without context: a future reader will otherwise wonder why a
threshold-computing protocol is called `BehaviourProvider` rather than something naming what it
actually does.

**Added 2026-09-14**: GitHub issue #131 (unimplemented) plans reorganising `extensions/` into
provider-kind-named subfolders — `extensions/themes/` for `LedThemeProvider`, `extensions/vehicles/`
for the planned `VehicleProvider` — but doesn't name a subfolder for a third kind, since it predates
this ADR. This feature pre-adopts that convention for its own kind: the reference
`BehaviourProvider` extension lives at `extensions/behaviours/dynamic_charging_threshold.py`, not
flat in `extensions/`. Whoever eventually picks up #131 should find `extensions/behaviours/`
already following the pattern it establishes, not something to migrate.

**Added 2026-09-15**: the config-file side of this same "keyed by provider kind" shape gets the
same treatment. #159 originally landed with a top-level `threshold_extension:` field on
`AppConfig` — a name specific to this one feature, the config-schema mirror of the
`ThresholdProvider`-vs-`BehaviourProvider` naming question this ADR already settled for the code
side. Reshaped before merge into `extensions.threshold:`, under a new `ExtensionsConfig` model
keyed by provider kind, so the still-unbuilt `VehicleProvider` (or any future kind) adds its own
field there (`extensions.vehicles:` or similar) rather than needing another top-level,
feature-specific config key. `led:` is deliberately untouched — LED's own `extensions:` list
(themes resolved via `resolve()`/`resolve_fallback()`) is a different shape for a different reason
(multiple simultaneous themes, priority-ordered) and this ADR does not fold it into
`AppConfig.extensions`.
