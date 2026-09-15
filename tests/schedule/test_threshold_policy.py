from common.constants import ELECTRICITY_VAT_RATE
from schedule.threshold_policy import EffectiveLimit, ThresholdPolicy


def test_threshold_policy_uses_the_dynamic_value_when_it_is_below_the_static_limit() -> (
    None
):
    # Static limit is 100p -- if the dynamic value weren't winning, this
    # would come back labelled "static", not "dynamic".
    policy = ThresholdPolicy(static_limit_incl_vat=100)

    limit = policy.effective_limit(5.0)

    assert limit == EffectiveLimit(5.0, 5.0 / ELECTRICITY_VAT_RATE, "dynamic")


def test_threshold_policy_clamps_the_dynamic_value_to_the_static_limit_when_it_is_above_it() -> (
    None
):
    # Static limit is 20p -- if the clamp weren't applied, this would come
    # back as the raw 50p dynamic value instead of the clamped 20p static one.
    policy = ThresholdPolicy(static_limit_incl_vat=20)

    limit = policy.effective_limit(50.0)

    assert limit == EffectiveLimit(20, 20 / ELECTRICITY_VAT_RATE, "static cap")


def test_threshold_policy_labels_the_source_as_dynamic_when_the_dynamic_value_equals_the_static_limit() -> (
    None
):
    # Tie-break (Copilot review, PR #168): when dynamic equals static
    # exactly, the source must still be "dynamic", not "static cap" -- the
    # numeric limit is identical either way, so only the label distinguishes
    # a `<=` tie-break from a `<` one.
    policy = ThresholdPolicy(static_limit_incl_vat=20)

    limit = policy.effective_limit(20.0)

    assert limit == EffectiveLimit(20, 20 / ELECTRICITY_VAT_RATE, "dynamic")


def test_threshold_policy_uses_the_static_limit_when_no_fresh_dynamic_value_is_available() -> (
    None
):
    # No fresh dynamic value this cycle and a non-zero static limit -- the
    # static limit is used as-is, labelled "static" (not "static cap", which
    # is reserved for when a clamp actually occurred against a fresh value).
    policy = ThresholdPolicy(static_limit_incl_vat=20)

    limit = policy.effective_limit(None)

    assert limit == EffectiveLimit(20, 20 / ELECTRICITY_VAT_RATE, "static")


def test_threshold_policy_uses_the_dynamic_value_as_is_when_the_static_limit_is_the_deferral_opt_out() -> (
    None
):
    # Static limit 0 is the explicit "defer fully to the extension" opt-out
    # (ADR 0022) -- a fresh dynamic value passes through unclamped, even
    # though it's numerically far above what a non-zero static would allow.
    policy = ThresholdPolicy(static_limit_incl_vat=0)

    limit = policy.effective_limit(40.0)

    assert limit == EffectiveLimit(40.0, 40.0 / ELECTRICITY_VAT_RATE, "dynamic")


def test_threshold_policy_uses_the_cached_dynamic_value_when_the_static_limit_is_the_deferral_opt_out_and_nothing_fresh_this_cycle() -> (
    None
):
    # Static limit 0 fully defers to the extension. The first call gets a
    # fresh 40p value, caching it inside the policy. The second call has
    # nothing fresh (None) -- the cached value from the prior call should be
    # used, labelled "cached dynamic".
    policy = ThresholdPolicy(static_limit_incl_vat=0)
    policy.effective_limit(40.0)

    limit = policy.effective_limit(None)

    assert limit == EffectiveLimit(40.0, 40.0 / ELECTRICITY_VAT_RATE, "cached dynamic")


def test_threshold_policy_reports_no_limit_available_on_a_cold_start_with_the_deferral_opt_out() -> (
    None
):
    # Static limit 0 fully defers to the extension, but nothing fresh has
    # arrived yet and nothing was cached from a prior call (cold start) --
    # the policy has no basis for a limit at all, so it reports None rather
    # than guessing.
    policy = ThresholdPolicy(static_limit_incl_vat=0)

    limit = policy.effective_limit(None)

    assert limit is None
