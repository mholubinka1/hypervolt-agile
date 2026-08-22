from pathlib import Path

import pytest
from pydantic import ValidationError

from config import ConfigLoader, Octopus, Schedule

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
