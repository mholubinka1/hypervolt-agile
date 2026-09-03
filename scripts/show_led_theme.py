"""Ephemeral test tool: pushes a custom LED theme YAML straight to the charger
and holds it until Ctrl+C, so a theme can be eyeballed on the real unit before
it is wired into config.

Usage:
    uv run python scripts/show_led_theme.py [--effect saints_fc] [--config-file config/config.yml]

`--effect` names a file under the repo's `themes/` folder -- the same folder the
app itself reads custom-theme colour maps from. The theme is loaded through
`hypervolt.led.load_custom_effect` -- the exact same parser the app uses -- so
what shows here is what the app would show.

Re-asserts brightness and the frame every hold cycle. If nothing lights up, the
main scheduler is probably running against the same charger and re-pushing its
own LED state every poll -- stop it and try again.

Ctrl+C clears the LED display (`effect_name: none`) and disconnects cleanly.

Like `calibrate_leds.py`, this deliberately does NOT use
`HypervoltChargerClient.create()`: that call clears the charger's active
charging schedule as a side effect, which is wrong for a throwaway diagnostic
with no relationship to scheduling. It connects only as much as needed to push
LED commands.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# scripts/ is a sibling of app/, so app/ has to be added to the path explicitly
# for `from hypervolt... import ...` / `from config import ...` to resolve --
# same shim as calibrate_leds.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from hypervolt.client.rest import HypervoltRestClient
from hypervolt.client.websocket import HypervoltWebSocketClient
from hypervolt.led import load_custom_effect
from hypervolt.state import HypervoltChargerStateDelta

from config import AppConfig, ConfigLoader

_HOLD_SECS = 15
_TARGET_BRIGHTNESS = 1.0
# themes/ sits at the repo root; this file is scripts/show_led_theme.py.
_THEMES_DIR = Path(__file__).resolve().parent.parent / "themes"


def _out(message: str) -> None:
    # Progress an operator watches live; flush each line so nothing is withheld
    # in a block buffer when stdout isn't a TTY (piped to `tee`, redirected).
    print(message, flush=True)


async def _ignore_state_update(_delta: HypervoltChargerStateDelta) -> None:
    # This tool only pushes; it doesn't react to charger state.
    return None


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
    # its clear_schedule() call -- see the module docstring.
    ws_client._connect_task = asyncio.create_task(ws_client.connect())
    try:
        await ws_client.wait_until_connected(timeout=30)
    except Exception:
        # A failed handshake still leaves the background connect task and the
        # REST client's session open -- clean both up before propagating. Each
        # step is guarded so one raising doesn't skip the other and mask the
        # original handshake failure.
        try:
            await ws_client.disconnect()
        except Exception as e:
            _out(f"Failed to disconnect websocket: {type(e).__name__}: {e}")
        try:
            await rest_client.close()
        except Exception as e:
            _out(f"Failed to close REST client: {type(e).__name__}: {e}")
        raise
    return rest_client, ws_client


async def _push_theme(
    ws_client: HypervoltWebSocketClient, leds: list[dict[str, float]]
) -> None:
    # Brightness is resent alongside every frame, not just once: if anything
    # else (most likely the main scheduler) is re-pushing its own brightness,
    # a one-shot push only wins the race for a moment.
    await ws_client.set_led_brightness(_TARGET_BRIGHTNESS)
    await ws_client.set_led_effect("steady_array", leds=leds)


def _resolve_effect_path(effect: str) -> Path:
    # Custom-theme colour maps live in the repo's themes/ folder -- resolve
    # against the same folder the app reads (app/main.py), so this previews
    # the real file.
    return _THEMES_DIR / f"{effect}.yaml"


async def run(config_file: Path, effect: str) -> None:
    effect_path = _resolve_effect_path(effect)
    if not effect_path.is_file():
        _out(f"No such theme file: {effect_path}")
        raise SystemExit(1)
    leds = load_custom_effect(effect_path)
    _out(f"Loaded {effect!r} ({len(leds)} LEDs) from {effect_path}.")

    app_config = ConfigLoader(config_file).get_config()
    rest_client, ws_client = await _connect(app_config)
    _out(
        f"Connected to charger {rest_client.charger.id}. Holding {effect!r} at "
        f"full brightness. Press Ctrl+C to clear and exit."
    )
    try:
        while True:
            await _push_theme(ws_client, leds)
            await asyncio.sleep(_HOLD_SECS)
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Ctrl+C during asyncio.run() typically raises KeyboardInterrupt in the
        # event loop's driving code, not inside this suspended coroutine;
        # asyncio.run()'s cleanup then cancels this task, delivering
        # CancelledError here instead. Catch both so the message and cleanup
        # below run regardless of which arrives.
        _out("\nStopping -- clearing LEDs.")
    finally:
        # Each step runs even if an earlier one raises (e.g. the websocket
        # already dropped) -- a best-effort shutdown that doesn't leave the
        # REST session open just because clearing the display failed first.
        try:
            await ws_client.set_led_effect("none")
        except Exception as e:
            _out(f"Failed to clear LED display: {type(e).__name__}: {e}")
        try:
            await ws_client.disconnect()
        except Exception as e:
            _out(f"Failed to disconnect websocket: {type(e).__name__}: {e}")
        try:
            await rest_client.close()
        except Exception as e:
            _out(f"Failed to close REST client: {type(e).__name__}: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--effect",
        type=str,
        default="saints_fc",
        help="Theme file stem under themes/ (default: saints_fc)",
    )
    parser.add_argument(
        "--config-file",
        type=str,
        default="config/config.yml",
        help="Path to config.yml (default: config/config.yml)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(Path(args.config_file), args.effect))
    except KeyboardInterrupt:
        # Guards the rare case where the interrupt lands outside any suspended
        # await in run() -- the coroutine's own except/finally already did the
        # real cleanup, so this only stops a raw traceback on a normal Ctrl+C.
        pass


if __name__ == "__main__":
    main()
