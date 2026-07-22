# Issues: chore/downgrade-websocket-reconnect-log

## Downgrade "Websocket reconnected" to DEBUG

**Issue**: #45

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

In `HypervoltProtocol._on_login_response`, split the current single `logger.info(...)` ternary into an `if self._reconnecting: / else:` block so the routine reconnect message logs at DEBUG while the one-off initial login message stays at INFO. No other logging statement in the WebSocket/protocol layer changes.

### Acceptance criteria

- [x] Given DEBUG is off, when the WebSocket reconnects, then "Websocket reconnected." does not appear in the console.
- [x] Given DEBUG is off, when the app starts and logs in for the first time, then "Websocket login successful." still appears at INFO.
- [x] Given DEBUG is on, when the WebSocket reconnects, then "Websocket reconnected." appears at DEBUG.
- [x] Given a login failure or any WebSocket error occurs, then it still surfaces at its existing WARNING/ERROR level, unchanged.

### Verification

Manually verified by calling `HypervoltProtocol._on_login_response` directly with a stubbed `send_message`/`on_state_update`, toggling `_reconnecting` and the console handler level, and asserting on captured log output:

- INFO level, `_reconnecting=False` → `"Websocket login successful."` logged at INFO.
- INFO level, `_reconnecting=True` → no output (reconnect message suppressed).
- DEBUG level, `_reconnecting=True` → `"Websocket reconnected."` logged at DEBUG.
- INFO level, `authenticated=False` → `"Websocket login failed."` still logged at ERROR, unchanged.

---
