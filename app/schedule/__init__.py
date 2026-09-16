import logging.config
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from logging import Logger, getLogger
from zoneinfo import ZoneInfo

from common.constants import APP_NAME, ELECTRICITY_VAT_RATE, TIMEZONE
from common.extensions import ExtensionWrapper
from common.logging import config
from common.model import ChargeSession, Price
from octopus.client import AgileClient
from schedule.builder import ScheduleBuilder
from schedule.threshold_policy import ThresholdPolicy

from config import AppConfig

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


@dataclass(frozen=True)
class _RebuildTrigger:
    """Per-trigger behaviour injected into the shared `Scheduler._rebuild`
    routine.

    `prepare` decides -- and may itself log -- whether to proceed at all,
    and returns the prices to build the schedule against, or `None` to skip
    (having already logged why). It is where each trigger's own
    "should we even try" logic lives; the shared routine has no built-in
    notion of it. `commit` runs immediately before the schedule is built,
    once an effective limit is confirmed available. The new-prices trigger
    needs this: its own `prepare` compares the fetched price horizon
    against `_time_until` to decide whether to even attempt a rebuild, so
    writing `_time_until`/`_agile_prices` any earlier would make a skipped
    cycle (e.g. a cold start with no limit yet) look identical to "already
    seen" on the next call, permanently suppressing the retry (PR #168).
    The replug trigger has no such self-referential pre-check -- it always
    attempts a rebuild while invalidated, regardless of the price horizon
    -- so an early write carries no equivalent risk; it writes its own
    watermarks unconditionally inside `prepare` instead (unchanged from
    this trigger's behaviour before this refactor), and `commit` is a
    no-op for it. `finalize` runs after a schedule has been successfully
    built and logged.
    """

    no_prices_warning: str
    no_threshold_warning: str
    success_log_prefix: str
    exception_message: str
    prepare: Callable[[list[Price]], list[Price] | None]
    commit: Callable[[list[Price]], None]
    finalize: Callable[[], None]


class Scheduler:
    def __init__(
        self,
        agile_client: AgileClient,
        config: AppConfig,
        threshold_provider: ExtensionWrapper | None = None,
    ) -> None:
        self._agile_client = agile_client
        self._timezone = TIMEZONE
        self._update_freq = config.schedule.frequency
        self._threshold_provider = threshold_provider
        self._threshold_policy = ThresholdPolicy(config.schedule.limit)
        self._builder = ScheduleBuilder(
            duration_hrs=config.schedule.duration,
            limit_exc_vat=config.schedule.limit / ELECTRICITY_VAT_RATE,
        )

        self._agile_prices: list[Price] = []
        self._time_until: datetime = datetime.now(ZoneInfo("UTC"))
        self._last_schedule_update: datetime | None = None
        self._last_schedule_verify: datetime | None = None
        self._schedule: list[ChargeSession] = []
        self._average_price_per_kwh: float | None = None
        self._invalidated: bool = False

    @property
    def schedule(self) -> list[ChargeSession]:
        return self._schedule

    @property
    def average_price_per_kwh(self) -> float | None:
        return self._average_price_per_kwh

    @property
    def timezone(self) -> str:
        return self._timezone

    def invalidate(self) -> None:
        self._invalidated = True

    async def update(self) -> None:
        await self._update_charging_schedule()
        self._prune_schedule()

    def should_verify(self) -> bool:
        _now = datetime.now(ZoneInfo("UTC"))
        if (
            not self._last_schedule_verify
            or _now - self._last_schedule_verify > timedelta(minutes=self._update_freq)
        ):
            self._last_schedule_verify = _now
            return True
        return False

    def _should_update(self) -> bool:
        _now = datetime.now(ZoneInfo("UTC"))
        if not self._last_schedule_update:
            self._last_schedule_update = _now
            return True
        if _now - self._last_schedule_update > timedelta(minutes=self._update_freq):
            self._last_schedule_update = _now
            return True
        return False

    def _prune_schedule(self) -> None:
        _now = datetime.now(ZoneInfo("UTC"))
        _expired = [s for s in self._schedule if s.end < _now]
        self._schedule = [s for s in self._schedule if s.end >= _now]
        for session in _expired:
            logger.info(f"Session expired: {session.format(self._timezone)}.")
        if _expired:
            logger.info(
                f"Pruned {len(_expired)} expired session(s), {len(self._schedule)} remaining."
            )
        elif self._schedule:
            logger.debug(
                f"Schedule intact: {len(self._schedule)} session(s) remaining."
            )

    async def _update_charging_schedule(self) -> None:
        if self._invalidated:
            await self._rebuild_on_replug()
        elif self._should_update():
            await self._rebuild_on_new_prices()

    async def _rebuild_on_replug(self) -> None:
        # Sampled before the shared routine's price-fetch await, matching
        # this trigger's behaviour before the #172 refactor -- prepare()
        # closes over this rather than re-sampling after the await, since a
        # slow fetch would otherwise shift which periods pass the from-now
        # filter and how fresh _last_schedule_update looks (Copilot review,
        # PR #175).
        _now = datetime.now(ZoneInfo("UTC"))

        def _prepare(new_prices: list[Price]) -> list[Price]:
            # No "has anything changed" pre-check here -- a replug always
            # rebuilds while invalidated, regardless of the price horizon.
            self._agile_prices = new_prices
            self._time_until = max(price.valid_to for price in new_prices)
            self._last_schedule_update = _now
            return [p for p in new_prices if p.valid_to > _now]

        def _finalize() -> None:
            self._invalidated = False

        await self._rebuild(
            _RebuildTrigger(
                no_prices_warning="No Agile prices returned. Skipping schedule rebuild.",
                no_threshold_warning=(
                    "price_limit_incl_vat is 0 and the threshold extension has "
                    "no fresh or cached value yet. Skipping schedule rebuild on "
                    "car plugged in until it produces one."
                ),
                success_log_prefix="New Schedule created on car plugged in",
                exception_message="Failed to rebuild schedule on car plugged in.",
                prepare=_prepare,
                commit=lambda new_prices: None,
                finalize=_finalize,
            )
        )

    async def _rebuild_on_new_prices(self) -> None:
        def _prepare(new_prices: list[Price]) -> list[Price] | None:
            # The "new price horizon vs old" pre-check: this is the one
            # thing the replug trigger has no equivalent of, so it lives
            # here rather than in the shared routine, which has no built-in
            # notion of "unchanged".
            _new_time_until = max(price.valid_to for price in new_prices)
            if not _new_time_until > self._time_until:
                logger.debug("Agile prices unchanged.")
                return None
            logger.info(
                f"New Agile prices received: {len(new_prices)} periods, "
                f"valid until {_new_time_until}."
            )
            return new_prices

        def _commit(new_prices: list[Price]) -> None:
            # _agile_prices / _time_until are deliberately not committed
            # until a limit is actually available -- otherwise a cold-start
            # skip (limit is None) would still advance them, making an
            # unchanged price horizon on the next cycle look identical to
            # the one just "seen" and short-circuit above before ever
            # asking the threshold provider again (Copilot review, PR #168).
            self._agile_prices = new_prices
            self._time_until = max(price.valid_to for price in new_prices)

        await self._rebuild(
            _RebuildTrigger(
                no_prices_warning="No Agile prices returned. Skipping schedule update.",
                no_threshold_warning=(
                    "price_limit_incl_vat is 0 and the threshold extension has "
                    "no fresh or cached value yet. Skipping schedule update "
                    "until it produces one."
                ),
                success_log_prefix="New schedule created",
                exception_message="Failed to create charging schedule.",
                prepare=_prepare,
                commit=_commit,
                finalize=lambda: None,
            )
        )

    async def _rebuild(self, trigger: _RebuildTrigger) -> None:
        try:
            _new_prices = await self._agile_client.get_upcoming_prices()
            if not _new_prices:
                logger.warning(trigger.no_prices_warning)
                return
            _prices_for_build = trigger.prepare(_new_prices)
            if _prices_for_build is None:
                return
            _dynamic_limit: float | None = None
            if self._threshold_provider is not None:
                _dynamic_limit = await self._threshold_provider.invoke("get_threshold")
            _limit = self._threshold_policy.effective_limit(_dynamic_limit)
            if _limit is None:
                logger.warning(trigger.no_threshold_warning)
                return
            trigger.commit(_new_prices)
            self._builder.update_limit(_limit.exc_vat)
            self._schedule, self._average_price_per_kwh = self._builder.build(
                _prices_for_build,
            )
            logger.info(
                f"{trigger.success_log_prefix}: {len(self._schedule)} sessions "
                f"(limit {_limit.incl_vat:.2f}p/kWh incl VAT, source: {_limit.source})."
            )
            for session in self._schedule:
                logger.info(f"Session: {session.format(self._timezone)}.")
            trigger.finalize()
        except Exception:
            logger.exception(trigger.exception_message)
