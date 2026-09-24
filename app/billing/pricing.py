"""Pure Decimal pricing of reported categories. Never consult catalog or feedback."""

from decimal import Decimal

MILLION = Decimal(1_000_000)


def estimate(native, runner, schedule, *, expected_model=None):
    reasons = []
    usage = native or runner
    basis = "native" if native else "opencode_normalized" if runner else "unreported"
    result = {
        "version": 1,
        "basis": basis,
        "status": "unavailable",
        "knownCost": None,
        "currency": "USD",
        "categories": [],
        "reasons": reasons,
        "rateSnapshot": schedule,
    }
    if not usage:
        reasons.append("usage_not_reported")
        return result
    if runner:
        reasons.extend(
            [
                "runner_billing_incomplete",
                "request_and_retry_usage_unknown",
                "runner_zero_fills_unknowns",
            ]
        )
    if not schedule:
        reasons.append("rate_schedule_unavailable")
        return result
    if schedule["serviceTier"] != "all" and (
        runner or usage.get("serviceTier") != schedule["serviceTier"]
    ):
        reasons.append("service_tier_unknown_or_unmatched")
        return result
    if (
        native
        and expected_model is not None
        and usage.get("reportedModelId") != expected_model
    ):
        reasons.append("returned_model_identity_unknown_or_unmatched")
        return result
    tiers = schedule["tiers"]
    tier = tiers[0] if len(tiers) == 1 else None
    if tier is None and native:
        context = native.get("inputTokens")
        if native["provider"] == "anthropic":
            parts = [
                context,
                native.get("cacheReadTokens"),
                native.get("cacheWriteTokens"),
            ]
            context = sum(parts) if all(v is not None for v in parts) else None
        if context is not None:
            tier = next(
                (
                    t
                    for t in tiers
                    if t["upToInputTokens"] is None or context <= t["upToInputTokens"]
                ),
                None,
            )
    if tier is None:
        reasons.append("per_request_tier_unavailable")
        return result
    rates = tier["rates"]
    result["tier"] = tier["upToInputTokens"]
    inp, out, cache = (
        usage.get(k) for k in ("inputTokens", "outputTokens", "cacheReadTokens")
    )
    if runner:
        counts = {
            "input": inp,
            "output": out,
            "reasoning": usage.get("reasoningTokens"),
            "cache_read": cache,
            "cache_write": usage.get("cacheWriteTokens"),
        }
        # OpenCode discards Anthropic cache-write TTL. Never assume 5-minute pricing.
        if usage["provider"] == "anthropic" and counts["cache_write"] != 0:
            rates = {k: v for k, v in rates.items() if k != "cache_write"}
    else:
        if usage["provider"] != "anthropic":
            inp = inp - cache if inp is not None and cache is not None else None
        counts = {"input": inp, "output": out, "cache_read": cache}
        if usage["provider"] == "anthropic":
            if usage.get("cacheWriteTokens") == 0:
                counts.update(cache_write_5m=0, cache_write_1h=0)
            else:
                counts.update(
                    cache_write_5m=usage.get("cacheWrite5MTokens"),
                    cache_write_1h=usage.get("cacheWrite1HTokens"),
                )
        elif usage["provider"] == "google":
            counts["reasoning"] = usage.get("reasoningTokens")
        elif usage.get("cacheWriteTokens") not in (None, 0):
            # No native OpenAI write category is defined by the v1 profile.
            counts["input"] = None
            reasons.append("unsupported_cache_write_category")
    if (
        native
        and usage.get("reportedTotalTokens") is not None
        and all(v is not None for v in counts.values())
        and sum(counts.values()) != usage["reportedTotalTokens"]
    ):
        reasons.append("reported_total_has_unpriced_categories")
    charges = []
    for category, count in counts.items():
        rate = rates.get(category)
        cost = None
        if count is None or count < 0:
            reasons.append(f"{category}_usage_unknown")
        elif count == 0:
            cost = Decimal(0)
        elif rate is None:
            reasons.append(f"{category}_rate_unknown")
        else:
            cost = Decimal(count) * Decimal(rate) / MILLION
        if cost is not None:
            charges.append(cost)
        result["categories"].append(
            {
                "category": category,
                "tokens": count,
                "ratePerMillion": rate,
                "cost": str(cost) if cost is not None else None,
            }
        )
    # Known zero categories alone do not supply a useful partial monetary estimate.
    useful = any(c["cost"] is not None and c["tokens"] for c in result["categories"])
    complete = not reasons
    if complete or useful:
        result["knownCost"] = str(sum(charges, Decimal(0)))
        result["status"] = "complete" if complete else "partial"
    return result


def usage_status(native, runner):
    """Coverage of captured counters, separate from prices and final execution status."""
    u = native or runner
    if not u:
        return "unavailable"
    fields = ["inputTokens", "outputTokens", "cacheReadTokens"]
    if runner or u["provider"] == "google":
        fields.append("reasoningTokens")
    if runner or u["provider"] == "anthropic":
        fields.append("cacheWriteTokens")
    known = [u.get(k) is not None for k in fields]
    if not any(known):
        return "unavailable"
    return "complete" if all(known) and not runner else "partial"
