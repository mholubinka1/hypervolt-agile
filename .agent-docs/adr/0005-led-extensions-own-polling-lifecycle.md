# LED theme extensions own their polling lifecycle; the scheduler never awaits their I/O

LED Theme Control (Feature 19) lets an operator register a `LedThemeProvider` extension whose
`resolve(now)` decides whether a dynamic theme (e.g. a football fixture) applies right now. The
scheduler calls this once per poll cycle (`poll_every_secs`, default 10s, configurable down to
2s) from `ScheduleCoordinator.run()` — the same single coroutine that also drives lock control
and schedule pushes, with no concurrency between cycles.

Marking `resolve()` `async` does not, by itself, stop a slow or hanging call from blocking that
cycle's other work: there is only one task running the loop, so an inline `await` on a live
network call stalls lock control and schedule pushes right along with LED state, every cycle,
until it returns.

Instead of bounding this with a timeout around `resolve()`, `LedThemeProvider` gets an optional
`async def start(self) -> None` lifecycle hook, called once when extensions are loaded at
startup. An extension needing live data (an HTTP poll, a sensor read) starts its own background
`asyncio.create_task()` inside `start()`, on its own cadence, and caches the result on itself.
`resolve(now)` then does nothing but return that cached value — a synchronous-in-effect,
never-blocking read. No timeout is needed anywhere, because nothing in the scheduler's call path
can hang. This follows the same pattern already used for the websocket client, which runs its
own connection loop as an independent background task rather than being awaited inline from the
scheduler.
