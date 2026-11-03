import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from schedule.fuel_threshold_recorder import FuelThresholdRecorder
from schedule.threshold_policy import EffectiveLimit
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import StaticPool

_DASHBOARD_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "grafana" / "fuel-threshold.json"
)
_UTC = ZoneInfo("UTC")
_RANGE_FROM = "'2026-11-03 09:00:00'"
_RANGE_TO = "'2026-11-08 12:00:00'"


def _dashboard() -> dict[str, Any]:
    return json.loads(_DASHBOARD_PATH.read_text(encoding="utf-8"))


def _panel_titled(title_prefix: str) -> dict[str, Any]:
    return next(
        panel
        for panel in _dashboard()["panels"]
        if panel["title"].startswith(title_prefix)
    )


def _query(panel: dict[str, Any], ref_id: str) -> str:
    _target = next(t for t in panel["targets"] if t["refId"] == ref_id)
    return (
        _target["rawSql"]
        .replace("$__timeFrom()", _RANGE_FROM)
        .replace("$__timeTo()", _RANGE_TO)
    )


async def _database_with_recorded_limits(*limits_incl_vat: float) -> Engine:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    _clock = datetime(2026, 11, 3, 10, 0, tzinfo=_UTC)
    for _index, _limit in enumerate(limits_incl_vat):
        recorder = FuelThresholdRecorder(
            engine, clock=lambda _at=_clock + timedelta(minutes=30 * _index): _at
        )
        await recorder.record(EffectiveLimit(_limit, _limit / 1.05, "dynamic"))
    return engine


async def test_agile_prices_panel_draws_the_latest_effective_limit_as_a_flat_line_across_the_time_range() -> (
    None
):
    engine = await _database_with_recorded_limits(12.5, 18.0)
    panel = _panel_titled("Agile Prices")

    with engine.connect() as connection:
        rows = list(connection.execute(text(_query(panel, "B"))))

    assert [str(row[0]) for row in rows] == [
        "2026-11-03 09:00:00",
        "2026-11-08 12:00:00",
    ]
    assert [float(row[1]) for row in rows] == [18.0, 18.0]


def _effective_limit_override(panel: dict[str, Any]) -> dict[str, Any]:
    _override = next(
        o
        for o in panel["fieldConfig"]["overrides"]
        if o["matcher"] == {"id": "byFrameRefID", "options": "B"}
    )
    return {p["id"]: p["value"] for p in _override["properties"]}


def test_effective_limit_line_is_dashed_unfilled_and_hidden_from_legend_and_tooltip() -> (
    None
):
    properties = _effective_limit_override(_panel_titled("Agile Prices"))

    assert properties["custom.lineStyle"]["fill"] == "dash"
    assert properties["custom.fillOpacity"] == 0
    assert properties["custom.hideFrom"] == {
        "legend": True,
        "tooltip": True,
        "viz": False,
    }


def test_agile_prices_panel_keeps_the_existing_price_query_and_annotation_binding() -> (
    None
):
    dashboard = _dashboard()
    panel = _panel_titled("Agile Prices")

    price_query = next(t for t in panel["targets"] if t["refId"] == "A")["rawSql"]
    annotation_panel_ids = {
        annotation["filter"]["ids"][0]
        for annotation in dashboard["annotations"]["list"]
        if "filter" in annotation
    }
    assert "product_rate" in price_query and "agile_forecast" in price_query
    assert panel["id"] in annotation_panel_ids


def test_dashboard_is_importable_with_the_shared_mysql_datasource_input() -> None:
    dashboard = _dashboard()

    assert [i["name"] for i in dashboard["__inputs"]] == ["DS_MYSQL"]
    assert dashboard["uid"] == "fuel-threshold"
    assert any(v["name"] == "region" for v in dashboard["templating"]["list"])
