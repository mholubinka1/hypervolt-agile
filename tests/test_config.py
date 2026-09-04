from pathlib import Path

import pytest
from pydantic import ValidationError

from config import (
    BuiltInLedTheme,
    ConfigLoader,
    CustomLedTheme,
    ExtensionEntry,
    LedConfig,
    Octopus,
    Schedule,
)

_VALID_CONFIG_YAML = """
octopus:
  account_number: "A-123"
  api_key: "sk_test"
hypervolt:
  username: "user@example.com"
  password: "secret"
schedule:
  total_charge_duration: 4
  price_limit_incl_vat: 15
  update_every_mins: 30
  poll_every_secs: 10
"""


def test_config_loader_parses_a_valid_config_file(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text(_VALID_CONFIG_YAML, encoding="utf-8")

    app_config = ConfigLoader(config_file).get_config()

    assert app_config.octopus.account_number == "A-123"
    assert app_config.hypervolt.username == "user@example.com"
    assert app_config.schedule.duration == 4
    assert app_config.schedule.limit == 15
    assert app_config.schedule.frequency == 30
    assert app_config.schedule.poll == 10
    assert app_config.log_level == "INFO"


def test_config_loader_exits_on_missing_required_section(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "hypervolt:\n  username: u\n  password: p\n", encoding="utf-8"
    )

    with pytest.raises(SystemExit) as exc_info:
        ConfigLoader(config_file)

    assert exc_info.value.code == 1


def test_config_loader_exits_when_file_is_missing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        ConfigLoader(tmp_path / "does-not-exist.yml")

    assert exc_info.value.code == 1


@pytest.mark.parametrize("field", ["account_number", "api_key"])
def test_octopus_rejects_blank_credentials(field: str) -> None:
    values = {"account_number": "A-123", "api_key": "sk_test", field: "   "}

    with pytest.raises(ValidationError):
        Octopus(**values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("total_charge_duration", 0),
        ("total_charge_duration", 24.1),
        ("price_limit_incl_vat", 0),
        ("update_every_mins", 0),
        ("poll_every_secs", 1),
    ],
)
def test_schedule_rejects_out_of_range_values(field: str, value: float) -> None:
    values = {
        "total_charge_duration": 4,
        "price_limit_incl_vat": 15,
        "update_every_mins": 30,
        "poll_every_secs": 10,
        field: value,
    }

    with pytest.raises(ValidationError):
        Schedule(**values)


def test_led_config_defaults_brightness_to_half_when_omitted() -> None:
    assert LedConfig().brightness == 0.5


def test_led_config_accepts_full_brightness() -> None:
    assert LedConfig(brightness=1.0).brightness == 1.0


@pytest.mark.parametrize("brightness", [0, -0.1, 1.1])
def test_led_config_rejects_out_of_range_brightness(brightness: float) -> None:
    with pytest.raises(ValidationError):
        LedConfig(brightness=brightness)


def test_custom_led_theme_accepts_valid_date_strings() -> None:
    theme = CustomLedTheme(effect="peace", start="03-14", end="03-16 06:00")

    assert theme.effect == "peace"
    assert theme.start == "03-14"
    assert theme.end == "03-16 06:00"


@pytest.mark.parametrize("field", ["start", "end"])
def test_custom_led_theme_rejects_malformed_date_string(field: str) -> None:
    values = {"effect": "peace", "start": "03-14", "end": "03-16", field: "not-a-date"}

    with pytest.raises(ValidationError):
        CustomLedTheme(**values)


@pytest.mark.parametrize("effect", ["halloween_mode", "christmas_mode", "party_mode"])
def test_custom_led_theme_rejects_built_in_theme_name(effect: str) -> None:
    # A custom theme sharing a built-in's identity would make apply_led_state's
    # diffing unable to tell them apart if their windows ever overlapped.
    with pytest.raises(ValidationError):
        CustomLedTheme(effect=effect, start="03-14", end="03-16")


@pytest.mark.parametrize("field", ["start", "end"])
def test_custom_led_theme_rejects_leap_day(field: str) -> None:
    # Windows are materialised against arbitrary real years -- "02-29" would
    # crash resolve_theme() on any non-leap year, so it's rejected up front.
    values = {"effect": "peace", "start": "03-14", "end": "03-16", field: "02-29"}

    with pytest.raises(ValidationError):
        CustomLedTheme(**values)


def test_custom_led_theme_rejects_same_month_end_before_start() -> None:
    # end_month < start_month is a deliberate year-wrap (like party_mode); but
    # a same-month reversed pair (16 before 14) isn't a wrap -- it produces a
    # window whose end is chronologically before its start, which can never
    # match any date and would silently never activate.
    with pytest.raises(ValidationError):
        CustomLedTheme(effect="peace", start="03-16", end="03-14")


def test_custom_led_theme_accepts_a_genuine_year_wrap() -> None:
    theme = CustomLedTheme(effect="peace", start="12-15", end="01-05")

    assert theme.start == "12-15"
    assert theme.end == "01-05"


@pytest.mark.parametrize("effect", ["halloween_mode", "christmas_mode", "party_mode"])
def test_built_in_led_theme_accepts_a_known_built_in_name(effect: str) -> None:
    theme = BuiltInLedTheme(effect=effect, start="10-31", end="11-01 06:00")

    assert theme.effect == effect
    assert theme.start == "10-31"
    assert theme.end == "11-01 06:00"


def test_built_in_led_theme_rejects_an_unknown_effect_name() -> None:
    with pytest.raises(ValidationError):
        BuiltInLedTheme(effect="peace", start="03-14", end="03-16")


@pytest.mark.parametrize("field", ["start", "end"])
def test_built_in_led_theme_rejects_malformed_date_string(field: str) -> None:
    values = {
        "effect": "christmas_mode",
        "start": "12-24",
        "end": "12-31",
        field: "not-a-date",
    }

    with pytest.raises(ValidationError):
        BuiltInLedTheme(**values)


@pytest.mark.parametrize("field", ["start", "end"])
def test_built_in_led_theme_rejects_leap_day(field: str) -> None:
    values = {
        "effect": "christmas_mode",
        "start": "12-24",
        "end": "12-31",
        field: "02-29",
    }

    with pytest.raises(ValidationError):
        BuiltInLedTheme(**values)


def test_built_in_led_theme_rejects_same_month_end_before_start() -> None:
    with pytest.raises(ValidationError):
        BuiltInLedTheme(effect="christmas_mode", start="12-31", end="12-24")


def test_built_in_led_theme_accepts_a_genuine_year_wrap() -> None:
    theme = BuiltInLedTheme(effect="party_mode", start="12-31 06:00", end="01-01 06:00")

    assert theme.start == "12-31 06:00"
    assert theme.end == "01-01 06:00"


def test_led_config_defaults_built_in_themes_to_an_empty_list_when_omitted() -> None:
    assert LedConfig().built_in_themes == []


def test_led_config_accepts_built_in_themes() -> None:
    config = LedConfig(
        built_in_themes=[
            {"effect": "christmas_mode", "start": "12-24", "end": "12-31 06:00"}
        ]
    )

    assert config.built_in_themes == [
        BuiltInLedTheme(effect="christmas_mode", start="12-24", end="12-31 06:00")
    ]


def test_led_config_accepts_custom_themes() -> None:
    config = LedConfig(
        custom_themes=[{"effect": "saints_fc", "start": "08-01", "end": "05-31"}]
    )

    assert [t.effect for t in config.custom_themes] == ["saints_fc"]


def test_extension_entry_defaults_config_to_an_empty_dict_when_omitted() -> None:
    entry = ExtensionEntry(name="saints_fc")

    assert entry.config == {}
