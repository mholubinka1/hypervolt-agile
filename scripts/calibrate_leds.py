"""One-off diagnostic tool: lights the charger's LEDs cumulatively -- index 0,
then 0-1, then 0-2, and so on -- so an operator can read off the true physical
position of each index by eye as it joins the lit run, replacing the
interpolated best-guess layout every custom LED theme has been designed
against so far (see FEATURES.md Feature 22).

Usage:
    uv run python scripts/calibrate_leds.py [--config-file config/config.yml]

Loops continuously -- growing the lit run one LED at a time from index 0 to 50,
then wrapping back to a single lit LED at index 0 and building up again --
until stopped with Ctrl+C, which clears the LED display and disconnects
cleanly before exiting.

Deliberately does NOT use HypervoltChargerClient.create(): that call clears
the charger's active charging schedule as a side effect (correct for the main
scheduler, which immediately rebuilds and re-pushes a real one on its next
cycle; wrong for a diagnostic tool with no relationship to scheduling at all).
This script connects only as much as needed to push LED commands.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# main.py resolves `from config import ...` etc. because Python adds a
# directly-run script's own directory to sys.path[0] -- true when the app is
# launched as `python app/main.py`, but this script lives in scripts/, a
# sibling of app/, so the same imports need app/ added explicitly here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from hypervolt.client.protocol import HypervoltChargerStateUpdateCallback
from hypervolt.client.rest import HypervoltRestClient
from hypervolt.client.websocket import HypervoltWebSocketClient
from hypervolt.state import HypervoltChargerStateDelta

from config import AppConfig, ConfigLoader

_LED_COUNT = 51
_HOLD_SECS = 10
_TARGET_BRIGHTNESS = 1.0
_BRIGHTNESS_TOLERANCE = 0.02
_BRIGHTNESS_SETTLE_SECS = 2
_BRIGHTNESS_CONFIRM_TIMEOUT_SECS = 5
_WHITE = {"r": 1.0, "g": 1.0, "b": 1.0}
_BLACK = {"r": 0.0, "g": 0.0, "b": 0.0}


class _BrightnessTracker:
    """Tracks the charger's own confirmation of the LED brightness it last
    reported.

    A one-shot `set_led_brightness` push is fire-and-forget -- it doesn't
    prove the charger actually applied it, only that the message was sent.
    The main scheduler, if it happens to be running against the same charger
    at the same time, re-pushes its own brightness/effect every poll cycle
    and would silently win that fight. Reading the confirmed value back is
    the only way this script can tell the two situations apart.
    """

    def __init__(self) -> None:
        self.reported: float | None = None
        self._confirmed = asyncio.Event()

    def reset(self) -> None:
        # The websocket volunteers a state snapshot on connect, carrying the
        # brightness from *before* this script pushed anything. Call this
        # after the push and before asking for a fresh snapshot, so the
        # confirmation check only considers a reading taken afterwards.
        self.reported = None
        self._confirmed.clear()

    async def on_state_update(self, delta: HypervoltChargerStateDelta) -> None:
        if delta.led_brightness is not None:
            self.reported = delta.led_brightness
            self._confirmed.set()

    async def wait_for_confirmation(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._confirmed.wait(), timeout=timeout)
        except TimeoutError:
            # No confirmation arrived in time -- reported stays None, and the
            # caller treats that the same as an unconfirmed/wrong brightness.
            pass


def _frame_for(index: int) -> list[dict[str, float]]:
    # Cumulative: every LED from 0 up to and including the current index is
    # lit, so the operator watches the lit run grow one LED at a time rather
    # than chase a single moving dot. Wrapping back to index 0 lights only
    # LED 0 again, resetting the run so it builds up from scratch.
    return [dict(_WHITE) if i <= index else dict(_BLACK) for i in range(_LED_COUNT)]


async def _connect(
    config: AppConfig,
    on_state_update: HypervoltChargerStateUpdateCallback,
) -> tuple[HypervoltRestClient, HypervoltWebSocketClient]:
    rest_client = await HypervoltRestClient.create(
        username=config.hypervolt.username,
        password=config.hypervolt.password,
    )
    ws_client = HypervoltWebSocketClient(
        charger=rest_client.charger,
        access_token_callback=rest_client.get_access_token,
        on_state_update=on_state_update,
    )
    # Mirrors HypervoltChargerClient.create()'s own connection start-up, minus
    # its clear_schedule() call -- see module docstring.
    ws_client._connect_task = asyncio.create_task(ws_client.connect())
    try:
        await ws_client.wait_until_connected(timeout=30)
    except Exception:
        # A failed handshake still leaves the background connect task and the
        # REST client's own session open -- clean both up before propagating,
        # mirroring HypervoltChargerClient.create()'s own failure-path
        # cleanup in charger.py. Each step is independently guarded so one
        # raising (e.g. the websocket never actually opened) doesn't skip
        # the other and mask the original handshake failure with a new one.
        try:
            await ws_client.disconnect()
        except Exception as e:
            print(
                f"Failed to disconnect websocket: {type(e).__name__}: {e}", flush=True
            )
        try:
            await rest_client.close()
        except Exception as e:
            print(f"Failed to close REST client: {type(e).__name__}: {e}", flush=True)
        raise
    return rest_client, ws_client


async def _push_frame(ws_client: HypervoltWebSocketClient, index: int) -> None:
    # Brightness is resent alongside every frame, not just once at startup --
    # if anything else (most likely the main scheduler, if it's running
    # against the same charger) is re-pushing its own brightness, a one-shot
    # push at the start would only win the race for a moment. Resending every
    # cycle means calibration stays at full brightness whenever nothing else
    # is actively overriding it.
    await ws_client.set_led_brightness(_TARGET_BRIGHTNESS)
    await ws_client.set_led_effect("steady_array", leds=_frame_for(index))
    # flush: an operator watches these scroll past live, but Python
    # block-buffers stdout whenever it isn't a TTY (piped to `tee`, redirected
    # to a log), which would hide every line until the script exits.
    print(f"Index {index}", flush=True)


async def run(config_file: Path) -> None:
    app_config = ConfigLoader(config_file).get_config()
    brightness = _BrightnessTracker()
    rest_client, ws_client = await _connect(app_config, brightness.on_state_update)
    print(
        f"Connected to charger {rest_client.charger.id}. Press Ctrl+C to stop.",
        flush=True,
    )
    try:
        index = 0
        await _push_frame(ws_client, index)

        # Read the charger's own confirmation of the brightness just pushed
        # -- a fire-and-forget push doesn't prove anything actually applied.
        # The settle sleep lets the connect-time snapshot AND the echo of our
        # own brightness `sync.apply` (both arrive as ordinary state updates)
        # land first; resetting only after that means the value we then wait
        # for is the fresh `sync.snapshot` we explicitly request, not our own
        # echo -- so a scheduler holding brightness down is still caught.
        await asyncio.sleep(_BRIGHTNESS_SETTLE_SECS)
        brightness.reset()
        await ws_client.sync_charger_state()
        await brightness.wait_for_confirmation(_BRIGHTNESS_CONFIRM_TIMEOUT_SECS)
        if (
            brightness.reported is None
            or abs(brightness.reported - _TARGET_BRIGHTNESS) > _BRIGHTNESS_TOLERANCE
        ):
            print(
                f"Warning: charger reports LED brightness "
                f"{brightness.reported!r}, not {_TARGET_BRIGHTNESS}. If the "
                "main scheduler is running against this charger too, it is "
                "likely re-pushing its own brightness/effect -- stop it "
                "before calibrating.",
                flush=True,
            )

        while True:
            await asyncio.sleep(_HOLD_SECS)
            index = (index + 1) % _LED_COUNT
            await _push_frame(ws_client, index)
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Ctrl+C during asyncio.run() typically raises KeyboardInterrupt in
        # the event loop's own driving code, not inside this suspended
        # coroutine -- asyncio.run()'s cleanup then cancels this task,
        # delivering CancelledError here instead. Catching both means the
        # friendly message and the cleanup below run regardless of which one
        # actually arrives.
        print("\nStopping -- clearing LEDs.", flush=True)
    finally:
        # Each step runs even if an earlier one raises (e.g. the websocket
        # already dropped) -- a best-effort shutdown that doesn't leave the
        # REST client's session open just because clearing the display
        # failed first.
        try:
            await ws_client.set_led_effect("none")
        except Exception as e:
            print(f"Failed to clear LED display: {type(e).__name__}: {e}", flush=True)
        try:
            await ws_client.disconnect()
        except Exception as e:
            print(
                f"Failed to disconnect websocket: {type(e).__name__}: {e}", flush=True
            )
        try:
            await rest_client.close()
        except Exception as e:
            print(f"Failed to close REST client: {type(e).__name__}: {e}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-file",
        type=str,
        default="config/config.yml",
        help="Path to config.yml (default: config/config.yml)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(Path(args.config_file)))
    except KeyboardInterrupt:
        # Guards the rare case where the interrupt lands outside any
        # suspended await in run() -- the coroutine's own except/finally
        # above already did the real cleanup either way, so this only stops
        # a raw traceback from reaching the terminal on a normal Ctrl+C exit.
        pass


if __name__ == "__main__":
    main()
