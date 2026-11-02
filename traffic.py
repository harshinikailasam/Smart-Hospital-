"""
traffic.py

Defines the three hospital traffic types and generates a realistic,
randomized time series of bandwidth demand for each -- this time series
*is* your smart-hospital traffic-mix dataset (the concrete artifact from
your research gap). It gets saved to results/traffic_dataset.csv so you
can point to an actual file when your professor asks "where's your dataset?"

Fixes vs. the earlier version:
  - generate_traffic() used to just print ever-increasing numbers forever
    and was never connected to calculate_utilization(). Now
    generate_time_series() returns real data that the router (routing.py)
    and simulator (simulate.py) actually consume.
  - Traffic now varies randomly within realistic bounds instead of growing
    without limit.
"""

import random
import csv

random.seed(42)  # reproducible results for your report/paper

# Each traffic type: where it starts/ends, its clinical priority
# (higher = more critical), and its typical bandwidth range in Mbps.
TRAFFIC_TYPES = {
    "ICU": {
        "source": "ICU_Monitor",
        "destination": "Ward_Gateway",
        "priority": 3,          # highest -- life-critical
        "base_bandwidth": 10,
        "variation": 2,         # steady, small variation
    },
    "TELEMEDICINE": {
        "source": "Telemedicine",
        "destination": "Ward_Gateway",
        "priority": 2,          # important, not life-critical
        "base_bandwidth": 30,
        "variation": 15,        # bursty video calls
    },
    "IOT": {
        "source": "IoT_Sensors",
        "destination": "Ward_Gateway",
        "priority": 1,          # lowest -- general sensors
        "base_bandwidth": 5,
        "variation": 8,         # bursty, occasional spikes
    },
}


def print_traffic_types():
    for name, d in TRAFFIC_TYPES.items():
        print(
            f"{name} -> Source: {d['source']} | Destination: {d['destination']} "
            f"| Priority: {d['priority']} | Base bandwidth: {d['base_bandwidth']} Mbps"
        )


def generate_time_series(num_steps=20):
    """Generate `num_steps` snapshots of demand for each traffic type.

    Returns:
        list of dicts, one per time step:
        [{"ICU": 11.3, "TELEMEDICINE": 34.7, "IOT": 6.1}, ...]
    """
    series = []
    for _ in range(num_steps):
        snapshot = {}
        for name, d in TRAFFIC_TYPES.items():
            demand = d["base_bandwidth"] + random.uniform(-d["variation"], d["variation"])
            # occasional traffic spike (e.g. a code blue, or a burst of sensor readings)
            if random.random() < 0.1:
                demand *= random.uniform(1.5, 2.2)
            snapshot[name] = max(0.5, round(demand, 2))
        series.append(snapshot)
    return series


def save_dataset(series, path="results/traffic_dataset.csv"):
    """Save the generated traffic series to CSV -- this is your dataset file."""
    fieldnames = ["time_step"] + list(TRAFFIC_TYPES.keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t, snapshot in enumerate(series):
            row = {"time_step": t}
            row.update(snapshot)
            writer.writerow(row)
    print(f"Saved traffic dataset to {path} ({len(series)} time steps)")


if __name__ == "__main__":
    print_traffic_types()
    print()

    series = generate_time_series(num_steps=20)
    for t, snapshot in enumerate(series):
        print(f"Time {t}: {snapshot}")

    save_dataset(series)
