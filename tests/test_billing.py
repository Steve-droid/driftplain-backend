"""Synthetic rates only; never provider price or activation evidence."""

from decimal import Decimal
import pytest
from app.billing.pricing import estimate


def schedule(**rates):
    return {
        "version": 1,
        "rateVersion": "fixture-1",
        "source": "https://example.test/rates",
        "observedAt": "2026-09-24T00:00:00Z",
        "currency": "USD",
        "serviceTier": "standard",
        "tierEvidence": "Synthetic fixture, not provider pricing",
        "tiers": [
            {"upToInputTokens": None, "rates": {k: str(v) for k, v in rates.items()}}
        ],
    }


def native(provider, **kw):
    return {
        "provider": provider,
        "serviceTier": "standard",
        "inputTokens": 100,
        "outputTokens": 20,
        "cacheReadTokens": 40,
        "cacheWriteTokens": 0,
        "reasoningTokens": 5,
        "generationRequests": 1,
        "transportRetries": 0,
        **kw,
    }


@pytest.mark.parametrize(
    "provider,expected",
    [("openai", "0.000220"), ("anthropic", "0.000300"), ("google", "0.000230")],
)
def test_native_disjoint_billing(provider, expected):
    result = estimate(
        native(provider, cacheWrite5MTokens=0, cacheWrite1HTokens=0),
        None,
        schedule(input=2, output=3, reasoning=2, cache_read=1),
    )
    assert result["status"] == "complete"
    assert Decimal(result["knownCost"]) == Decimal(expected)


def test_anthropic_ttl_and_reasoning_in_output():
    result = estimate(
        native(
            "anthropic",
            cacheWriteTokens=30,
            cacheWrite5MTokens=10,
            cacheWrite1HTokens=20,
        ),
        None,
        schedule(input=2, output=3, cache_read=1, cache_write_5m=4, cache_write_1h=6),
    )
    assert Decimal(result["knownCost"]) == Decimal("0.000460")
    assert result["status"] == "complete"


def test_missing_usage_prices_and_zero():
    assert estimate(None, None, schedule(input=2))["knownCost"] is None
    assert estimate(native("openai"), None, None)["status"] == "unavailable"
    p = estimate(
        native("openai", cacheReadTokens=None), None, schedule(input=2, output=3)
    )
    assert p["status"] == "partial" and Decimal(p["knownCost"]) == Decimal("0.00006")
    z = estimate(
        native(
            "openai",
            inputTokens=0,
            outputTokens=0,
            cacheReadTokens=0,
            reasoningTokens=0,
        ),
        None,
        schedule(),
    )
    assert z["status"] == "complete" and Decimal(z["knownCost"]) == 0


def test_runner_counts_are_not_subtracted_twice_and_never_complete():
    s = schedule(input=2, output=3, reasoning=3, cache_read=1, cache_write=2)
    s["serviceTier"] = (
        "all"  # synthetic rates explicitly identical across service tiers
    )
    r = estimate(None, native("openai", billingComplete=False), s)
    assert Decimal(r["knownCost"]) == Decimal("0.000315")
    assert r["status"] == "partial"
    assert "runner_billing_incomplete" in r["reasons"]


def test_threshold_tier_native_and_unknown_runner():
    s = schedule(input=2, output=3, cache_read=1)
    s["tiers"].insert(
        0,
        {
            "upToInputTokens": 50,
            "rates": {"input": "1", "output": "1", "cache_read": "1"},
        },
    )
    assert Decimal(estimate(native("openai"), None, s)["knownCost"]) == Decimal(
        "0.000220"
    )
    assert estimate(None, native("openai"), s)["knownCost"] is None


def test_aborted_request_is_unknown_not_zero():
    assert (
        estimate(
            {"provider": "openai", "generationRequests": 1}, None, schedule(input=2)
        )["status"]
        == "unavailable"
    )


def test_tiers_are_never_assumed_and_runner_needs_tier_invariant_rates():
    u = native("openai", serviceTier=None)
    assert (
        estimate(u, None, schedule(input=1, output=2, cache_read=1))["knownCost"]
        is None
    )
    u["serviceTier"] = "priority"
    assert (
        estimate(u, None, schedule(input=1, output=2, cache_read=1))["knownCost"]
        is None
    )
    assert (
        estimate(None, native("openai"), schedule(input=1, output=2, cache_read=1))[
            "status"
        ]
        == "unavailable"
    )


def test_rate_validation_rejects_nonfinite_negative_and_unordered_tiers():
    from app.billing.rates import Schedule

    for value in ["NaN", "Infinity", "-1"]:
        with pytest.raises(ValueError):
            Schedule.model_validate(schedule(input=value))
    s = schedule(input=1)
    s["tiers"] = [
        {"upToInputTokens": 100, "rates": {}},
        {"upToInputTokens": 50, "rates": {}},
        {"upToInputTokens": None, "rates": {}},
    ]
    with pytest.raises(ValueError):
        Schedule.model_validate(s)


def test_returned_model_mismatch_or_missing_never_uses_requested_model_rates():
    for reported in [None, "wrong-model"]:
        r = estimate(
            native("openai", reportedModelId=reported),
            None,
            schedule(input=2, output=3, cache_read=1),
            expected_model="executed-model",
        )
        assert r["status"] == "unavailable" and r["knownCost"] is None
    r = estimate(
        native("openai", reportedModelId="executed-model"),
        None,
        schedule(input=2, output=3, cache_read=1),
        expected_model="executed-model",
    )
    assert r["status"] == "complete"
