from pathlib import Path

from hypervolt.led import THEMES_DIR, load_custom_themes_for_config

from config import LedConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_themes_dir_points_at_the_repo_themes_directory() -> None:
    assert THEMES_DIR == _REPO_ROOT / "themes"
    assert THEMES_DIR.is_dir()
    assert (THEMES_DIR / "saints_fc.yaml").is_file()


def test_the_app_no_longer_references_a_led_effects_directory() -> None:
    offenders = [
        py
        for py in (_REPO_ROOT / "app").rglob("*.py")
        if "led_effects" in py.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_a_custom_theme_entry_loads_its_map_from_the_themes_directory() -> None:
    led_config = LedConfig(
        custom_themes=[{"effect": "saints_fc", "start": "08-01", "end": "05-31"}]
    )

    result = load_custom_themes_for_config(led_config, THEMES_DIR)

    assert [theme.effect_name for theme, _, _ in result] == ["saints_fc"]
    assert result[0][0].leds is not None and len(result[0][0].leds) == 51
