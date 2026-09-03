import importlib.util
from pathlib import Path

from hypervolt.led import load_custom_effect

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_show_led_theme():
    _path = _REPO_ROOT / "scripts" / "show_led_theme.py"
    _spec = importlib.util.spec_from_file_location("_show_led_theme", _path)
    assert _spec and _spec.loader
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    return _module


_RED = {"r": 0xD7 / 255, "g": 0x19 / 255, "b": 0x20 / 255}  # Southampton red #D71920
_SAINTS_RED_INDICES = {
    1,
    2,
    5,
    6,
    21,
    22,
    24,
    25,
    39,
    40,
    42,
    43,
    44,
    48,
    49,
    50,
}


def test_shipped_saints_fc_map_paints_the_southampton_stripes() -> None:
    leds = load_custom_effect(_REPO_ROOT / "themes" / "saints_fc.yaml")

    assert len(leds) == 51
    red_indices = {i for i, led in enumerate(leds) if led == _RED}
    assert red_indices == _SAINTS_RED_INDICES


def test_show_led_theme_resolves_effect_files_from_the_repo_themes_directory() -> None:
    module = _load_show_led_theme()

    resolved = module._resolve_effect_path("saints_fc")

    assert resolved == _REPO_ROOT / "themes" / "saints_fc.yaml"
    assert "led_effects" not in resolved.parts


def test_no_led_effects_directory_remains_in_the_repo() -> None:
    assert not (_REPO_ROOT / "config" / "led_effects").exists()
