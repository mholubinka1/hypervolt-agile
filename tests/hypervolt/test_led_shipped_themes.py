from pathlib import Path

import pytest
from hypervolt.led import load_custom_effect

_LED_EFFECTS_DIR = Path(__file__).resolve().parents[2] / "config" / "led_effects"
_SHIPPED_THEMES = ["peace", "qe_ii", "diana", "st_george", "st_patricks"]


@pytest.mark.parametrize("name", _SHIPPED_THEMES)
def test_shipped_theme_parses_into_51_leds(name: str) -> None:
    leds = load_custom_effect(_LED_EFFECTS_DIR / f"{name}.yaml")

    assert len(leds) == 51
    assert all({"r", "g", "b"} == led.keys() for led in leds)
