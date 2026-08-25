# `resolve_theme` becomes `async def` to honestly await `LedThemeProvider.resolve()`

`LedThemeProvider.resolve(now)` is specced as `async def` — ADR 0005 makes it non-blocking in
practice (a cached-value read, with real I/O moved into `start()`'s background task), but it's
still a coroutine function. `resolve_theme()` (the free function in `led.py`) previously stayed
synchronous by construction, since only the tuple-based custom/built-in tiers existed. Adding the
extension tier means `resolve_theme` must `await` each extension's `resolve()` in turn, so it
becomes `async def` itself.

**Rejected alternative**: give `ExtensionWrapper` a synchronous `resolve_sync()` that just reads
the cached value, keeping `resolve_theme` sync. Rejected because it would leave
`LedThemeProvider.resolve()` declared `async def` in the protocol while the framework never
actually awaits it — a future extension author who puts real `await`-based logic directly inside
`resolve()` (instead of `start()`'s background task, as intended) would have that code silently
never run.

**Consequence**: `ScheduleCoordinator._apply_led_state` (already `async`) adds one `await`. All
existing `resolve_theme(...)` call sites in tests become `async def test_...` functions with
`await` — mechanical, since pytest-asyncio is already configured in auto mode.
