from pathlib import Path

from hypervolt.led import load_custom_themes

from config import CustomLedTheme


def _write_theme_yaml(led_effects_dir: Path, name: str, colour: str) -> None:
    led_effects_dir.mkdir(parents=True, exist_ok=True)
    (led_effects_dir / f"{name}.yaml").write_text(
        f'default_colour: "{colour}"\n', encoding="utf-8"
    )


def test_load_custom_themes_loads_a_valid_entry(tmp_path: Path) -> None:
    _write_theme_yaml(tmp_path, "peace", "#0057B7")
    entries = [CustomLedTheme(effect="peace", start="03-14", end="03-16")]

    result = load_custom_themes(entries, tmp_path)

    assert len(result) == 1
    theme, start, end = result[0]
    assert theme.effect_name == "peace"
    assert theme.leds is not None
    assert len(theme.leds) == 51
    assert start == (3, 14, 0, 0)
    assert end == (3, 16, 0, 0)


def test_load_custom_themes_drops_entry_with_missing_yaml_file(tmp_path: Path) -> None:
    _write_theme_yaml(tmp_path, "peace", "#0057B7")
    entries = [
        CustomLedTheme(effect="peace", start="03-14", end="03-16"),
        CustomLedTheme(effect="does-not-exist", start="04-01", end="04-02"),
    ]

    result = load_custom_themes(entries, tmp_path)

    assert len(result) == 1
    assert result[0][0].effect_name == "peace"


def test_load_custom_themes_drops_entry_with_malformed_yaml(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "broken.yaml").write_text("name: no-default-colour\n", encoding="utf-8")
    _write_theme_yaml(tmp_path, "peace", "#0057B7")
    entries = [
        CustomLedTheme(effect="broken", start="03-01", end="03-02"),
        CustomLedTheme(effect="peace", start="03-14", end="03-16"),
    ]

    result = load_custom_themes(entries, tmp_path)

    assert len(result) == 1
    assert result[0][0].effect_name == "peace"
