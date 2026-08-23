from pathlib import Path

import pytest
from hypervolt.led import load_custom_effect


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "theme.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_custom_effect_fills_all_leds_with_default_colour(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        name: peace
        default_colour: "#0057B7"
        """,
    )

    leds = load_custom_effect(path)

    assert len(leds) == 51
    assert all(led == {"r": 0.0, "g": 87 / 255, "b": 183 / 255} for led in leds)


def test_load_custom_effect_applies_segment_indices_over_default(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        """
        name: gold-accents
        default_colour: "#000000"
        segments:
          - colour: "#FFD700"
            indices: [0, 1, 2]
        """,
    )

    leds = load_custom_effect(path)

    _gold = {"r": 255 / 255, "g": 215 / 255, "b": 0.0}
    assert leds[0] == _gold
    assert leds[1] == _gold
    assert leds[2] == _gold
    assert leds[3] == {"r": 0.0, "g": 0.0, "b": 0.0}


def test_load_custom_effect_applies_segment_ranges_inclusively(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        name: gold-range
        default_colour: "#000000"
        segments:
          - colour: "#FFD700"
            ranges: [[10, 12]]
        """,
    )

    leds = load_custom_effect(path)

    _gold = {"r": 255 / 255, "g": 215 / 255, "b": 0.0}
    _black = {"r": 0.0, "g": 0.0, "b": 0.0}
    assert leds[9] == _black
    assert leds[10] == _gold
    assert leds[11] == _gold
    assert leds[12] == _gold
    assert leds[13] == _black


def test_load_custom_effect_later_segment_wins_on_overlap(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        name: overlap
        default_colour: "#000000"
        segments:
          - colour: "#FFD700"
            indices: [0]
          - colour: "#0057B7"
            indices: [0]
        """,
    )

    leds = load_custom_effect(path)

    assert leds[0] == {"r": 0.0, "g": 87 / 255, "b": 183 / 255}


def test_load_custom_effect_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_custom_effect(tmp_path / "does-not-exist.yaml")


def test_load_custom_effect_raises_when_default_colour_missing(tmp_path: Path) -> None:
    path = _write(tmp_path, "name: no-default\n")

    with pytest.raises(ValueError):
        load_custom_effect(path)


@pytest.mark.parametrize("colour", ["not-a-colour", "#GGGGGG"])
def test_load_custom_effect_raises_on_invalid_hex(tmp_path: Path, colour: str) -> None:
    path = _write(tmp_path, f'default_colour: "{colour}"\n')

    with pytest.raises(ValueError):
        load_custom_effect(path)


def test_load_custom_effect_raises_on_out_of_range_index(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        default_colour: "#000000"
        segments:
          - colour: "#FFD700"
            indices: [51]
        """,
    )

    with pytest.raises(IndexError):
        load_custom_effect(path)
