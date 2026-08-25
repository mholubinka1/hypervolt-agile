from hypervolt.led import load_built_in_themes_for_config

from config import LedConfig


def test_returns_empty_list_when_led_config_is_none() -> None:
    assert load_built_in_themes_for_config(None) == []


def test_returns_empty_list_when_built_in_themes_is_empty() -> None:
    assert load_built_in_themes_for_config(LedConfig()) == []


def test_loads_built_in_themes_from_led_config() -> None:
    led_config = LedConfig(
        built_in_themes=[
            {"effect": "christmas_mode", "start": "12-24", "end": "12-31 06:00"}
        ]
    )

    result = load_built_in_themes_for_config(led_config)

    assert len(result) == 1
    theme, start, end = result[0]
    assert theme.effect_name == "christmas_mode"
    assert theme.leds is None
    assert start == (12, 24, 0, 0)
    assert end == (12, 31, 6, 0)
