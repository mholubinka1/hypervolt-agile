from pathlib import Path

from hypervolt.led import load_custom_themes_for_config

from config import LedConfig


def test_returns_empty_list_when_led_config_is_none(tmp_path: Path) -> None:
    assert load_custom_themes_for_config(None, tmp_path) == []


def test_loads_custom_themes_from_led_config(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "peace.yaml").write_text(
        'default_colour: "#0057B7"\n', encoding="utf-8"
    )
    led_config = LedConfig(
        custom_themes=[{"effect": "peace", "start": "03-14", "end": "03-16"}]
    )

    result = load_custom_themes_for_config(led_config, themes_dir=tmp_path)

    assert len(result) == 1
    assert result[0][0].effect_name == "peace"
    assert result[0][0].leds is not None  # resolved from themes_dir/peace.yaml
