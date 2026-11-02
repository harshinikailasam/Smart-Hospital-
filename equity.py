"""
equity.py

The Equity Quality Index (EQI): a fairness floor so the router doesn't
optimize ICU traffic into perfection while starving everyone else.

We define "delivery ratio" for a traffic type at a time step as:
    delivered / demanded
where "delivered" is capped by how congested its path got (if a link on
the path is over 100% utilized, we treat that traffic as only partially
delivered, proportional to the overload).

EQI for a time step = the *lowest* delivery ratio across all traffic
types that step. A single starved traffic type drags the whole score
down -- which is exactly the point of a fairness metric.
"""

EQI_FLOOR = 0.7  # minimum acceptable delivery ratio for any traffic type


def delivery_ratio(G, path, demanded_bandwidth):
    """Estimate how much of the demanded bandwidth actually got through,
    based on the worst (most congested) link on the path."""
    worst_overload = 1.0
    for i in range(len(path) - 1):
        data = G[path[i]][path[i + 1]]
        if data["utilization"] > data["capacity"]:
            ratio = data["capacity"] / data["utilization"]
            worst_overload = min(worst_overload, ratio)
    return worst_overload


def compute_eqi(delivery_ratios):
    """delivery_ratios: dict like {"ICU": 1.0, "TELEMEDICINE": 0.8, "IOT": 0.5}
    Returns the EQI for this time step (the minimum ratio)."""
    if not delivery_ratios:
        return 1.0
    return min(delivery_ratios.values())


def check_equity_floor(delivery_ratios):
    """Returns a list of traffic types that fell below the fairness floor
    this time step -- these are the ones getting starved."""
    return [name for name, ratio in delivery_ratios.items() if ratio < EQI_FLOOR]
