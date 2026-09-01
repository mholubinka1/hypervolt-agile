"""One-off diagnostic tool: lights each of the charger's 51 LEDs in turn so an
operator can read off the true physical position of each index by eye,
replacing the interpolated best-guess layout every custom LED theme has been
designed against so far (see FEATURES.md Feature 22).

Usage:
    poetry run python scripts/calibrate_leds.py [--config-file config/config.yml]

Loops continuously -- index 0 through 50, wrapping back to 0 -- until stopped
with Ctrl+C, which clears the LED display and disconnects cleanly before
exiting.

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

from hypervolt.client.rest import HypervoltRestClient
from hypervolt.client.websocket import HypervoltWebSocketClient
from hypervolt.state import HypervoltChargerStateDelta

from config import AppConfig, ConfigLoader

_LED_COUNT = 51
_HOLD_SECS = 10
_WHITE = {"r": 1.0, "g": 1.0, "b": 1.0}
_BLACK = {"r": 0.0, "g": 0.0, "b": 0.0}


async def _ignore_state_update(_delta: HypervoltChargerStateDelta) -> None:
    # This script only ever pushes LED commands, never reads charger state --
    # the websocket client requires a state-update callback regardless, so
    # this is a deliberate no-op rather than wiring up state tracking nothing
    # here needs.
    return None


def _frame_for(index: int) -> list[dict[str, float]]:
    return [dict(_WHITE) if i == index else dict(_BLACK) for i in range(_LED_COUNT)]


async def _connect(
    config: AppConfig,
) -> tuple[HypervoltRestClient, HypervoltWebSocketClient]:
    rest_client = await HypervoltRestClient.create(
        username=config.hypervolt.username,
        password=config.hypervolt.password,
    )
    ws_client = HypervoltWebSocketClient(
        charger=rest_client.charger,
        access_token_callback=rest_client.get_access_token,
        on_state_update=_ignore_state_update,
    )
    # Mirrors HypervoltChargerClient.create()'s own connection start-up, minus
    # its clear_schedule() call -- see module docstring.
    ws_client._connect_task = asyncio.create_task(ws_client.connect())
    await ws_client.wait_until_connected(timeout=30)
    return rest_client, ws_client


async def run(config_file: Path) -> None:
    app_config = ConfigLoader(config_file).get_config()
    rest_client, ws_client = await _connect(app_config)
    print(f"Connected to charger {rest_client.charger.id}. Press Ctrl+C to stop.")
    try:
        await ws_client.set_led_brightness(1.0)
        index = 0
        while True:
            await ws_client.set_led_effect("steady_array", leds=_frame_for(index))
            print(f"Index {index}")
            await asyncio.sleep(_HOLD_SECS)
            index = (index + 1) % _LED_COUNT
    except KeyboardInterrupt:
        print("\nStopping -- clearing LEDs.")
    finally:
        await ws_client.set_led_effect("none")
        await ws_client.disconnect()
        await rest_client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-file",
        type=str,
        default="config/config.yml",
        help="Path to config.yml (default: config/config.yml)",
    )
    args = parser.parse_args()
    asyncio.run(run(Path(args.config_file)))


if __name__ == "__main__":
    main()
