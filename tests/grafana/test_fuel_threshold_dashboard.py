import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from schedule.fuel_threshold_recorder import FuelThresholdRecorder
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
        .replace(
            "$__timeFilter(recorded_at)",
            f"recorded_at BETWEEN {_RANGE_FROM} AND {_RANGE_TO}",
        )
    )


async def _database_with_records(*records: tuple[float, float | None]) -> Engine:
    # Each record is (effective limit incl VAT, fuel price in pence per litre).
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await FuelThresholdRecorder(engine).prepare()
    _clock = datetime(2026, 11, 3, 10, 0, tzinfo=_UTC)
    # Rows are inserted directly: the recorder only writes rows with a price,
    # but the dashboard must cope with the price-less rows older versions wrote.
    with engine.begin() as connection:
        for _index, (_limit, _fuel_price) in enumerate(records):
            connection.execute(
                text(
                    "INSERT INTO fuel_price_threshold (recorded_at, "
                    "fuel_price_pence_per_litre, dynamic_threshold_incl_vat, "
                    "effective_limit_incl_vat, limit_source) "
                    "VALUES (:at, :price, :threshold, :limit, 'dynamic')"
                ),
                {
                    "at": (_clock + timedelta(minutes=30 * _index)).replace(
                        tzinfo=None
                    ),
                    "price": _fuel_price,
                    "threshold": None if _fuel_price is None else _limit,
                    "limit": _limit,
                },
            )
    return engine


async def test_agile_prices_panel_draws_the_latest_effective_limit_as_a_flat_line_across_the_time_range() -> (
    None
):
    engine = await _database_with_records((12.5, None), (18.0, None))
    panel = _panel_titled("Agile Prices")

    with engine.connect() as connection:
        rows = list(connection.execute(text(_query(panel, "B"))))

    assert [str(row[0]) for row in rows] == [
        "2026-11-03 09:00:00",
        "2026-11-08 12:00:00",
    ]
    assert [float(row[1]) for row in rows] == [18.0, 18.0]


async def test_fuel_price_gauge_shows_the_latest_recorded_fuel_price_in_pence_per_litre() -> (
    None
):
    # The newest row has no fuel price (e.g. a static-limit cycle) -- the gauge
    # must skip it rather than blank out.
    engine = await _database_with_records((20.0, 141.5), (22.0, 143.9), (10.0, None))
    panel = _panel_titled("Fuel price (latest)")

    with engine.connect() as connection:
        rows = list(connection.execute(text(_query(panel, "A"))))

    assert panel["type"] == "gauge"
    assert [float(row[0]) for row in rows] == [143.9]


async def test_fuel_price_timeline_plots_fuel_price_and_effective_limit_over_time() -> (
    None
):
    engine = await _database_with_records((20.0, 141.5), (18.0, None), (22.0, 143.9))
    panel = _panel_titled("Fuel price vs effective charging limit")

    with engine.connect() as connection:
        rows = list(connection.execute(text(_query(panel, "A"))))

    assert panel["type"] == "timeseries"
    assert [
        (row.fuel_price_p_per_litre, float(row.effective_limit_p_per_kwh))
        for row in rows
    ] == [(141.5, 20.0), (None, 18.0), (143.9, 22.0)]


def test_fuel_price_timeline_puts_the_charging_limit_on_its_own_axis_and_looks_back_beyond_the_dashboard_range() -> (
    None
):
    panel = _panel_titled("Fuel price vs effective charging limit")

    limit_override = next(
        o
        for o in panel["fieldConfig"]["overrides"]
        if o["matcher"]["options"] == "effective_limit_p_per_kwh"
    )
    properties = {p["id"]: p["value"] for p in limit_override["properties"]}
    assert properties["custom.axisPlacement"] == "right"
    assert properties["unit"] == "p/kwh"
    assert properties["custom.axisLabel"] == "p/kWh"
    fuel_override = next(
        o
        for o in panel["fieldConfig"]["overrides"]
        if o["matcher"]["options"] == "fuel_price_p_per_litre"
    )
    fuel_properties = {p["id"]: p["value"] for p in fuel_override["properties"]}
    assert fuel_properties["custom.axisLabel"] == "p/L"
    assert panel["timeFrom"]


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


def test_fuel_price_timeline_leaves_a_gap_where_no_price_was_recorded() -> None:
    # No row is written while the fuel price is unavailable, so rows more than
    # an hour apart must break the price line -- bridging them would draw a
    # fuel price that was never observed.
    panel = _panel_titled("Fuel price vs effective charging limit")

    price_override = next(
        o
        for o in panel["fieldConfig"]["overrides"]
        if o["matcher"]["options"] == "fuel_price_p_per_litre"
    )
    properties = {p["id"]: p["value"] for p in price_override["properties"]}
    assert properties["custom.spanNulls"] is False
    assert panel["fieldConfig"]["defaults"]["custom"]["insertNulls"] == 60 * 60 * 1000
