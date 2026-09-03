import json
from pathlib import Path

import pytest

_MAP_PATH = (
    Path(__file__).resolve().parents[2]
    / "themes"
    / "reference"
    / "charger_led_map.json"
)

_BODY_W_MM = 243
_BODY_H_MM = 328
_RING_REGIONS = {
    "ring-top",
    "ring-top-left-corner",
    "ring-left",
    "ring-bottom-left-corner",
    "ring-bottom",
    "ring-bottom-right-corner",
    "ring-right",
    "ring-top-right-corner",
}
_BOLT_SEGMENTS = {"lower-blade", "hook", "upper-blade"}
_EXPECTED_SEGMENT = {
    **{i: "lower-blade" for i in (39, 40, 41)},
    **{i: "hook" for i in (42, 43, 44)},
    **{i: "upper-blade" for i in range(45, 51)},
}
_DARK_INDICES = set(range(20, 27))


@pytest.fixture(scope="module")
def led_map() -> dict:
    return json.loads(_MAP_PATH.read_text(encoding="utf-8"))


def test_every_led_index_0_to_50_is_present_once(led_map: dict) -> None:
    assert sorted(int(k) for k in led_map) == list(range(51))


def test_positions_sit_within_the_charger_body(led_map: dict) -> None:
    for entry in led_map.values():
        assert 0 <= entry["x_mm"] <= _BODY_W_MM
        assert 0 <= entry["y_mm"] <= _BODY_H_MM


def test_regions_are_ring_regions_or_bolt(led_map: dict) -> None:
    assert {e["region"] for e in led_map.values()} <= _RING_REGIONS | {"bolt"}


def test_bolt_segment_is_present_exactly_for_bolt_leds(led_map: dict) -> None:
    for index, entry in led_map.items():
        if entry["region"] == "bolt":
            assert entry["bolt_segment"] == _EXPECTED_SEGMENT[int(index)]
        else:
            assert "bolt_segment" not in entry


def test_only_the_dead_bottom_run_is_marked_not_live(led_map: dict) -> None:
    dark = {int(k) for k, e in led_map.items() if not e["live"]}
    assert dark == _DARK_INDICES


def test_no_two_lit_leds_share_a_position(led_map: dict) -> None:
    lit_positions = [(e["x_mm"], e["y_mm"]) for e in led_map.values() if e["live"]]
    assert len(lit_positions) == len(set(lit_positions))
