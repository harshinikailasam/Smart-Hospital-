"""
explainability.py

Every time the agentic router reroutes traffic away from the baseline
(shortest-hop) path, it should say *why* in plain English. This module
is that whole contribution -- deliberately simple, on purpose. A reader
should be able to open explainability_log.txt and understand every
decision the network made without knowing any AI/ML at all.
"""

LOG_PATH = "results/explainability_log.txt"


def explain_decision(time_step, traffic_type, baseline_path, chosen_path,
                      congestion_percent, log_file=None):
    """Write (and return) a one-line, human-readable justification for a
    routing decision. Only called when the agent's path differs from the
    baseline, so the log only fills up with *meaningful* decisions."""

    if baseline_path == chosen_path:
        return None  # nothing to explain -- agent agreed with the baseline

    message = (
        f"[t={time_step}] Rerouted {traffic_type} traffic away from the "
        f"standard path {' -> '.join(baseline_path)} "
        f"(congestion reached {congestion_percent:.0f}%). "
        f"Used {' -> '.join(chosen_path)} instead to protect latency."
    )

    print(message)
    if log_file:
        log_file.write(message + "\n")
    return message


def open_log(path=LOG_PATH):
    return open(path, "w")
