import math
from typing import NamedTuple

from common.constants import ELECTRICITY_VAT_RATE


class EffectiveLimit(NamedTuple):
    incl_vat: float
    exc_vat: float
    source: str


class ThresholdPolicy:
    """Owns ADR 0022's effective charging-price-limit decision: the static
    `price_limit_incl_vat` config value is an ultimate ceiling over a
    dynamic threshold extension's value, with a cache of the last fresh
    dynamic value (for cycles where the extension has nothing new) and a
    cold-start skip (when static is the explicit opt-out value `0` and
    nothing is available yet, fresh or cached).
    """

    def __init__(self, static_limit_incl_vat: float) -> None:
        self._static_limit_incl_vat = static_limit_incl_vat
        self._cached_dynamic_limit_incl_vat: float | None = None

    def effective_limit(
        self, dynamic_limit_incl_vat: float | None
    ) -> EffectiveLimit | None:
        dynamic_limit_incl_vat = self._sanitised(dynamic_limit_incl_vat)
        if dynamic_limit_incl_vat is not None:
            self._cached_dynamic_limit_incl_vat = dynamic_limit_incl_vat

        if self._static_limit_incl_vat == 0:
            return self._deferred_limit(dynamic_limit_incl_vat)
        return self._capped_limit(dynamic_limit_incl_vat)

    @staticmethod
    def _sanitised(dynamic_limit_incl_vat: float | None) -> float | None:
        # A misbehaving threshold extension may return a non-finite or
        # non-positive value; treat it exactly as "nothing fresh this
        # cycle" rather than let it become the effective limit.
        if dynamic_limit_incl_vat is None:
            return None
        if math.isfinite(dynamic_limit_incl_vat) and dynamic_limit_incl_vat > 0:
            return dynamic_limit_incl_vat
        return None

    def _deferred_limit(
        self, dynamic_limit_incl_vat: float | None
    ) -> EffectiveLimit | None:
        # static_limit_incl_vat == 0 is the explicit "defer fully to the
        # extension" opt-out (ADR 0022) -- no static ceiling to clamp
        # against, so a fresh value passes through unclamped.
        if dynamic_limit_incl_vat is not None:
            return self._limit(dynamic_limit_incl_vat, "dynamic")
        if self._cached_dynamic_limit_incl_vat is not None:
            return self._limit(self._cached_dynamic_limit_incl_vat, "cached dynamic")
        return None

    def _capped_limit(self, dynamic_limit_incl_vat: float | None) -> EffectiveLimit:
        # static_limit_incl_vat is an ultimate ceiling (ADR 0022): a fresh
        # dynamic value wins only up to that ceiling; otherwise static is
        # used as-is.
        if dynamic_limit_incl_vat is None:
            return self._limit(self._static_limit_incl_vat, "static")
        _limit_incl_vat = min(dynamic_limit_incl_vat, self._static_limit_incl_vat)
        _source = (
            "dynamic"
            if dynamic_limit_incl_vat <= self._static_limit_incl_vat
            else "static cap"
        )
        return self._limit(_limit_incl_vat, _source)

    @staticmethod
    def _limit(limit_incl_vat: float, source: str) -> EffectiveLimit:
        return EffectiveLimit(
            limit_incl_vat, limit_incl_vat / ELECTRICITY_VAT_RATE, source
        )
