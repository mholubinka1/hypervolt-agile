# `resolve_theme` gains a second pass so one extension can rank both above and below the theme tiers

`resolve_theme`'s primary walk is a strict priority stack: every extension's `resolve()` (in
config list order), then custom themes, then built-in themes; first match wins. That order
bakes in "extensions always outrank custom and built-in themes". The Saints FC extension
(issue #117) breaks that assumption. Within its match window — `kick-off − 30 minutes …
kick-off + 3 hours` — the club strip should outrank every configured theme, exactly as the
primary walk already gives it. The rest of a fixture day it should do the opposite: sit
*below* every custom and built-in theme, so a New Year's Eve `always_on` strip still shows
all day and the Saints colours appear only as a charging-gated last resort. A single fixed
position in the priority stack cannot express "top priority now, bottom priority in three
hours".

**The decision.** `resolve_theme` runs a second pass. The primary walk is unchanged
(extensions → custom → built-in themes, first match wins). Only if it produces nothing does
`resolve_theme` then walk each extension's optional
`async def resolve_fallback(self, now) -> LedTheme | None` in config list order and take the
first non-`None`. Whichever pass produced the match then goes through the existing defensive
copy (deep-copied `leds`, `always_on` carried through), so both passes return the same shape.

`resolve_fallback` is declared the way `start` / `stop` already are (ADR 0005) — a comment on
the `LedThemeProvider` Protocol, not a Protocol member, so it is not structurally required —
and reached via `hasattr`. `ExtensionWrapper` exposes its own `resolve_fallback` that shares
one body (`_invoke`) with `resolve`: the same exception isolation, the same de-duplication of
a repeated identical warning, the same "raise `TypeError` if the return is neither `None` nor
an `LedTheme`" guard, and the same "recovered" info log. Each method tracks its own entry in
`self._last_exception` (keyed by method name), so a `resolve()` that fails every cycle keeps
its warning de-duplicated even while a `resolve_fallback()` that succeeds every cycle runs
alongside it — one path's recovery never masks or resets the other's. An extension without
the hook makes `ExtensionWrapper.resolve_fallback` return `None`, so it contributes nothing
to the second pass. A failure in one extension's `resolve_fallback` is isolated and the next
extension is still consulted, mirroring the primary walk.

The Saints FC extension (issue #117) is the first and only planned implementer: `resolve()`
returns the strip (`always_on=True`) inside the match window and `None` otherwise;
`resolve_fallback()` returns the strip (`always_on=False`) on a match day *outside* every
window and `None` otherwise.

**The alternative rejected.** A static "priority: low" flag on an extension, or a separate
lowest tier below built-in themes that flagged extensions drop into. Rejected because the
same extension needs *both* ranks on the same day depending on the time — a static position,
by construction, gives it one. Two passes let `resolve()` keep the extension above the theme
tiers while `resolve_fallback()` places it below them, which is exactly the split the Saints
window rule requires.
