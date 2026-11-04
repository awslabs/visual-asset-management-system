# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-stage cost attribution for a benchmark run.

Cost is derived rather than recorded. The container deliberately reports only durations and the
instance type it landed on, because instance pricing is regional, changes over time, and differs by
purchase option -- baking a price into a pipeline run would produce a number that silently rots. The
rate is resolved here, at reporting time, and the derivation is kept separate from the lookup so the
arithmetic is testable without a network call.

Attribution is per stage because that is what makes the comparison actionable: a model that spends
forty seconds fetching weights and twenty generating is a different proposition from one that spends
all sixty generating, even though the run costs the same. The overhead stages are reported separately
for exactly that reason.
"""

import logging
from typing import Dict, Optional

from matrix import OVERHEAD_STAGES, STAGE_ORDER

logger = logging.getLogger(__name__)

SECONDS_PER_HOUR = 3600.0


def stage_costs(stage_seconds: Dict[str, float], hourly_rate: float) -> Dict[str, float]:
    """Cost per stage for one run, at the given instance rate.

    Every stage occupies the whole instance for its duration -- the job holds one GPU instance from
    start to finish -- so a stage's cost is its share of the wall clock at the instance's rate. This is
    not a claim about which stage used the GPU hardest; it is the cost of the time the instance was
    held while that stage ran.
    """
    if hourly_rate < 0:
        raise ValueError(f"An hourly rate cannot be negative: {hourly_rate}")
    per_second = hourly_rate / SECONDS_PER_HOUR
    return {stage: round(seconds * per_second, 6)
            for stage, seconds in stage_seconds.items()}


def cost_breakdown(stage_seconds: Dict[str, float], hourly_rate: float) -> dict:
    """A run's cost split into the model's own work and the overhead around it."""
    costs = stage_costs(stage_seconds, hourly_rate)
    model = {k: v for k, v in costs.items() if k not in OVERHEAD_STAGES}
    overhead = {k: v for k, v in costs.items() if k in OVERHEAD_STAGES}
    return {
        "hourlyRate": hourly_rate,
        "perStage": {stage: costs[stage] for stage in STAGE_ORDER if stage in costs},
        "modelCost": round(sum(model.values()), 6),
        "overheadCost": round(sum(overhead.values()), 6),
        "totalCost": round(sum(costs.values()), 6),
    }


def resolve_hourly_rates(instance_types, region: str, price_overrides=None) -> Dict[str, float]:
    """On-demand Linux hourly rates for the instance types a matrix actually used.

    Rates come from the AWS Price List API. An operator-supplied override wins, which is how a
    deployment with a Savings Plan, a Reserved Instance or negotiated pricing gets a cost report that
    reflects what it is really paying rather than the public rate. An instance whose rate cannot be
    resolved is omitted rather than defaulted: a report is more useful admitting it does not know a
    price than quoting a wrong one as fact.
    """
    rates = dict(price_overrides or {})
    missing = [t for t in sorted(set(instance_types)) if t and t not in rates]
    if not missing:
        return rates

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        logger.warning(
            "boto3 is unavailable, so no instance rates could be resolved and the report will carry "
            "durations without costs for %s", ", ".join(missing))
        return rates

    retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
    # The Price List API is only offered in a few regions regardless of the region being priced.
    client = boto3.client("pricing", region_name="us-east-1", config=retry_config)
    for instance_type in missing:
        rate = _lookup_on_demand_rate(client, instance_type, region)
        if rate is None:
            logger.warning(
                "No on-demand rate found for %s in %s; its stages will be reported without a cost",
                instance_type, region)
            continue
        rates[instance_type] = rate
    return rates


def _lookup_on_demand_rate(client, instance_type: str, region: str) -> Optional[float]:
    """The shared on-demand Linux rate for one instance type, or ``None`` when it cannot be read."""
    import json

    filters = [
        {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
        {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
        {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
    ]
    try:
        response = client.get_products(ServiceCode="AmazonEC2", Filters=filters, MaxResults=1)
    except Exception as e:  # a pricing lookup must not fail the report
        logger.warning("Pricing lookup failed for %s: %s", instance_type, e)
        return None

    for document in response.get("PriceList", []):
        product = json.loads(document) if isinstance(document, str) else document
        for term in (product.get("terms", {}).get("OnDemand") or {}).values():
            for dimension in (term.get("priceDimensions") or {}).values():
                price = (dimension.get("pricePerUnit") or {}).get("USD")
                if price is not None:
                    try:
                        return float(price)
                    except (TypeError, ValueError):
                        continue
    return None
