import asyncio
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

from config import DatabaseConfig

logger: Logger = getLogger(APP_NAME)

_DATABASE_TIMEOUT_SECS = 5

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
    Column("dynamic_threshold_incl_vat", Numeric(9, 6), nullable=True),
    Column("effective_limit_incl_vat", Numeric(9, 6), nullable=False),
    Column("limit_source", String(20), nullable=False),
    Index("ix_fuel_price_threshold_recorded_at", "recorded_at"),
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FuelThresholdRecorder:
    def __init__(
        self, engine: Engine, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._table_ready = False

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

    async def record(
        self,
        effective_limit: EffectiveLimit,
        fuel_price_pence_per_litre: float | None = None,
        dynamic_threshold_incl_vat: float | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._insert,
                effective_limit,
                fuel_price_pence_per_litre,
                dynamic_threshold_incl_vat,
            )
        except Exception as e:
            logger.warning(
                f"Could not record the fuel price threshold: {type(e).__name__}."
            )

    def _insert(
        self,
        effective_limit: EffectiveLimit,
        fuel_price_pence_per_litre: float | None,
        dynamic_threshold_incl_vat: float | None,
    ) -> None:
        if not self._table_ready:
            _metadata.create_all(self._engine, checkfirst=True)
            self._table_ready = True
        _recorded_at = self._clock().astimezone(UTC).replace(tzinfo=None)
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
