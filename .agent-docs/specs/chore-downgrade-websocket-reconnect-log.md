# Downgrade Websocket Reconnected Log to DEBUG

## Problem Statement

Production logs on the Pi (`docker logs hypervolt-agile-scheduler`) are dominated by a "Websocket reconnected." INFO line that fires roughly every 54 minutes under completely normal operation — the WebSocket connection to the Hypervolt charger is cycled routinely, not just on failure. This drowns out the INFO-level lines that actually matter (schedule creation, session confirmation, price updates) and makes the log harder to scan.

## Solution

Reclassify the routine "Websocket reconnected." message as DEBUG, so it no longer appears at the default INFO console level but remains available when DEBUG logging is enabled. The sibling "Websocket login successful." message (the one-off initial startup login) stays at INFO, since it only fires once per process lifetime and is a useful startup signal. No error or warning path changes — failed logins, lock command failures, schedule.set errors, and connection-lost warnings all continue to surface at their current levels.

## User Stories

1. As the operator monitoring the Pi via `docker logs`, I want routine WebSocket reconnects to stay out of the INFO log stream, so that I can scan the log for meaningful events without noise.
2. As the operator debugging a connectivity issue, I want to be able to enable DEBUG logging and still see every reconnect event, so that I have full visibility when I need it.
3. As the operator relying on the scheduler, I want login failures and other WebSocket errors to keep surfacing at WARNING/ERROR exactly as they do today, so that real problems are never missed as a side effect of this change.

## Implementation Decisions

- **Module**: `app/hypervolt/client/protocol.py`, `HypervoltProtocol._on_login_response`.
- The existing single `logger.info(...)` call with a ternary between `"Websocket reconnected."` and `"Websocket login successful."` is replaced with an `if self._reconnecting: / else:` block:
  - `self._reconnecting` → `logger.debug("Websocket reconnected.")`
  - not reconnecting → `logger.info("Websocket login successful.")`
- No other logging statements in `protocol.py` or `websocket.py` change. In particular, `_on_login_response`'s failure branch (`logger.error("Websocket login failed.")`), `on_error` (warning/error), and the connection-lost/closed handling in `websocket.py` (`_receive_messages_worker`, `connect`, `_send_message`) are left exactly as they are.
- No config, schema, or public interface changes — `common/logging.py`'s level configuration is unaffected; this only changes the level passed at the call site.

## Testing Decisions

- This codebase has no automated test suite (verified: no `tests/` directory or `test_*.py` files exist). Verification is through execution, consistent with existing project convention.
- A full end-to-end run (real Octopus/Hypervolt credentials, a physical charger, and a live reconnect) is not available in the development environment, so verification exercises `_on_login_response` directly with stubbed `send_message`/`on_state_update` collaborators — the same seam the method's tests would use if this repo had a test suite — while toggling `_reconnecting` and the console handler's level, and asserting on the captured log output. This is the practical equivalent of "running the app" for this single handler.
- Confirm "Websocket reconnected." does not appear at the default INFO console level but does appear once the handler level is raised to DEBUG.
- Confirm "Websocket login successful." still appears at INFO regardless of DEBUG state.
- Confirm a failed login (`authenticated=False`) still logs at ERROR, unchanged.

## Out of Scope

- Any other INFO-level log lines (schedule creation, session confirmation, price updates) — these remain unchanged.
- Changing the default console or file log level (`LOG_LEVEL` / `FILE_LOG_LEVEL` in `common/constants.py`).
- Adding a test suite to the project.

## Further Notes

Raised from production log observation on the Pi deployment (`docker logs hypervolt-agile-scheduler`), where four "Websocket reconnected." lines appeared within a ~24 hour window purely from routine WebSocket cycling, alongside no actual connectivity errors.
