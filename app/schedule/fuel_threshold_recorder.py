import asyncio
import math
from collections.abc import Callable
from datetime import UTC, datetime
from logging import Logger, getLogger

from common.constants import APP_NAME
from schedule.threshold_policy import EffectiveLimit
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Engine,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    insert,
)
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from config import DatabaseConfig

logger: Logger = getLogger(APP_NAME)

_DATABASE_TIMEOUT_SECS = 5
_RECORD_TIMEOUT_SECS = 10
_MAX_FUEL_PRICE = 10000
_MAX_THRESHOLD = 1_000_000

_metadata = MetaData()
_fuel_price_threshold = Table(
    "fuel_price_threshold",
    _metadata,
    Column(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("recorded_at", DateTime, nullable=False),
    Column("fuel_price_pence_per_litre", Numeric(7, 3), nullable=True),
    Column("dynamic_threshold_incl_vat", Numeric(12, 6), nullable=True),
    Column("effective_limit_incl_vat", Numeric(12, 6), nullable=False),
    Column("limit_source", String(20), nullable=False),
    Index("ix_fuel_price_threshold_recorded_at", "recorded_at"),
)


def _storable(value: object, scale: int, upper_bound: float) -> float | None:
    # A value the Numeric column cannot hold would fail the whole insert and
    # lose the valid limit row, so an unusable one is stored as NULL instead.
    # The bound is checked after rounding to the column's scale: 9999.9996
    # is under 10000 but rounds up to it.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        _number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(_number):
        return None
    _rounded = round(_number, scale)
    return _rounded if 0 < _rounded < upper_bound else None


def _storable_pair(
    fuel_price: object, dynamic_threshold: object
) -> tuple[float | None, float | None]:
    _threshold = _storable(dynamic_threshold, 6, _MAX_THRESHOLD)
    # A price is only meaningful beside the threshold it came with.
    _price = (
        _storable(fuel_price, 3, _MAX_FUEL_PRICE) if _threshold is not None else None
    )
    return _price, _threshold


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FuelThresholdRecorder:
    def __init__(
        self,
        engine: Engine,
        clock: Callable[[], datetime] = _utc_now,
        timeout_secs: float = _RECORD_TIMEOUT_SECS,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._timeout_secs = timeout_secs
        self._table_ready = False
        self._last_fuel_price: float | None = None

    @classmethod
    def from_config(cls, database: DatabaseConfig) -> "FuelThresholdRecorder":
        _url = URL.create(
            "mysql+pymysql",
            username=database.user,
            password=database.password.get_secret_value(),
            host=database.host,
            port=database.port,
            database=database.name,
        )
        # PyMySQL has no read/write timeout by default, so a hung database
        # would stall the schedule rebuild this write runs inside.
        return cls(
            create_engine(
                _url,
                pool_pre_ping=True,
                connect_args={
                    "connect_timeout": _DATABASE_TIMEOUT_SECS,
                    "read_timeout": _DATABASE_TIMEOUT_SECS,
                    "write_timeout": _DATABASE_TIMEOUT_SECS,
                },
            )
        )

    def dispose(self) -> None:
        self._engine.dispose()

    async def prepare(self) -> None:
        await self._guarded(
            self._ensure_table, "prepare the fuel price threshold table"
        )

    async def record(
        self,
        effective_limit: EffectiveLimit,
        fuel_price_pence_per_litre: float | None = None,
        dynamic_threshold_incl_vat: float | None = None,
    ) -> None:
        _price, _threshold = _storable_pair(
            fuel_price_pence_per_litre, dynamic_threshold_incl_vat
        )
        if _price is not None:
            # Remembered before the write so a failing database is attempted
            # once per price change rather than once per cycle.
            self._last_fuel_price = _price
        await self._guarded(
            lambda: self._insert(effective_limit, _price, _threshold),
            "record the fuel price threshold",
        )

    def fuel_price_changed(
        self, fuel_price: object, dynamic_threshold_incl_vat: object
    ) -> bool:
        # Asks what a row for this pair would actually store, so a pair whose
        # price record() would drop (an unstorable threshold) is never a
        # change -- otherwise every cycle would write another price-less row.
        _price, _ = _storable_pair(fuel_price, dynamic_threshold_incl_vat)
        return _price is not None and _price != self._last_fuel_price

    async def _guarded(self, work: Callable[[], None], action: str) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(work), self._timeout_secs)
        except TimeoutError:
            logger.warning(
                f"Could not {action}: timed out after {self._timeout_secs:g}s."
            )
        except Exception as e:
            # Never the driver message: it can carry the host and user.
            _code = ""
            if isinstance(e, DBAPIError) and e.orig is not None:
                _first = e.orig.args[0] if e.orig.args else None
                if isinstance(_first, int):
                    _code = f" (MySQL error {_first})"
            logger.warning(f"Could not {action}: {type(e).__name__}{_code}.")

    def _ensure_table(self) -> None:
        if not self._table_ready:
            _metadata.create_all(self._engine, checkfirst=True)
            self._table_ready = True

    def _insert(
        self,
        effective_limit: EffectiveLimit,
        fuel_price_pence_per_litre: float | None,
        dynamic_threshold_incl_vat: float | None,
    ) -> None:
        # Stamped before the table check: the first write after an outage can
        # spend seconds in create_all, and the row belongs to the rebuild.
        _recorded_at = self._clock().astimezone(UTC).replace(tzinfo=None)
        self._ensure_table()
        with self._engine.begin() as connection:
            connection.execute(
                insert(_fuel_price_threshold).values(
                    recorded_at=_recorded_at,
                    fuel_price_pence_per_litre=fuel_price_pence_per_litre,
                    dynamic_threshold_incl_vat=dynamic_threshold_incl_vat,
                    effective_limit_incl_vat=effective_limit.incl_vat,
                    limit_source=effective_limit.source,
                )
            )
