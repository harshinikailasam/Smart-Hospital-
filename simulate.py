"""
simulate.py

The main driver. For every time step:
  1. Generate/load traffic demand for ICU, Telemedicine, and IoT.
  2. Route each flow twice -- once with the baseline (hop-count) router,
     once with the congestion-aware agent -- on two SEPARATE copies of
     the network, so we can compare them fairly.
  3. Log an explainability message whenever the agent's choice differs
     from the baseline.
  4. Compute the EQI (fairness score) for both scenarios.
  5. Save all results to results/ so you have real numbers and charts
     for your report, paper, and viva.

Traffic is routed in priority order (ICU first, then Telemedicine, then
IoT) each time step, since that's how a real network would allocate
capacity to higher-priority flows first.
"""

import csv
import argparse
import pandas as pd
import matplotlib.pyplot as plt

from topology import create_hospital_network, reset_utilization, add_load, link_utilization_percent
from traffic import TRAFFIC_TYPES, generate_time_series, save_dataset
from routing import baseline_path, congestion_aware_path, path_max_utilization_percent, adaptive_weight, WEIGHT
from explainability import explain_decision, open_log
from equity import delivery_ratio, compute_eqi, check_equity_floor, EQI_FLOOR

NUM_STEPS = 20


def run_scenario(G, traffic_series, use_agent, log_file=None, adaptive=False, weight=WEIGHT):
    """Run the full time series on graph G using either the baseline
    router (use_agent=False) or the congestion-aware agent (use_agent=True).
    Returns a list of per-time-step result dicts."""

    # Order flows by priority (highest first) so critical traffic gets
    # first pick of the best path each time step.
    ordered_types = sorted(TRAFFIC_TYPES.items(), key=lambda kv: -kv[1]["priority"])

    results = []
    prev_peak = 0

    for t, demand in enumerate(traffic_series):
        reset_utilization(G)  # each time step is a fresh snapshot of demand

        w = adaptive_weight(prev_peak, weight) if (use_agent and adaptive) else weight
        reroutes = 0
        delivery_ratios = {}
        max_util_this_step = 0

        for name, info in ordered_types:
            src, dst = info["source"], info["destination"]
            bandwidth = demand[name]

            base = baseline_path(G, src, dst)

            if use_agent:
                chosen = congestion_aware_path(G, src, dst, weight=w)
            else:
                chosen = base

            add_load(G, chosen, bandwidth)

            if use_agent:
                reroutes += int(chosen != base)
                congestion_pct = path_max_utilization_percent(G, base)
                explain_decision(t, name, base, chosen, congestion_pct, log_file)

            delivery_ratios[name] = delivery_ratio(G, chosen, bandwidth)
            max_util_this_step = max(
                max_util_this_step,
                max(link_utilization_percent(G).values())
            )

        eqi = compute_eqi(delivery_ratios)
        starved = check_equity_floor(delivery_ratios)

        prev_peak = max_util_this_step
        results.append({
            "time_step": t,
            "eqi": eqi,
            "max_link_utilization_pct": max_util_this_step,
            "reroutes": reroutes,
            "weight_used": w,
            "starved_traffic": ",".join(starved) if starved else "none",
            **{f"delivery_{k}": v for k, v in delivery_ratios.items()},
        })

    return results


def save_results(results, path):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f"Saved results to {path}")


def plot_comparison(baseline_results, agent_results, out="results/comparison.png"):
    df_base = pd.DataFrame(baseline_results)
    df_agent = pd.DataFrame(agent_results)

    fig, axes = plt.subplots(2, 1, figsize=(10, 8))

    axes[0].plot(df_base["time_step"], df_base["max_link_utilization_pct"],
                 label="Baseline (hop-count)", marker="o")
    axes[0].plot(df_agent["time_step"], df_agent["max_link_utilization_pct"],
                 label="Congestion-aware agent", marker="o")
    axes[0].axhline(100, color="red", linestyle="--", linewidth=1, label="Capacity limit")
    axes[0].set_title("Worst-case link utilization per time step")
    axes[0].set_xlabel("Time step")
    axes[0].set_ylabel("Utilization (%)")
    axes[0].legend()

    axes[1].plot(df_base["time_step"], df_base["eqi"],
                 label="Baseline (hop-count)", marker="o")
    axes[1].plot(df_agent["time_step"], df_agent["eqi"],
                 label="Congestion-aware agent", marker="o")
    axes[1].axhline(EQI_FLOOR, color="red", linestyle="--", linewidth=1, label="Equity floor")
    axes[1].set_title("Equity Quality Index (EQI) per time step")
    axes[1].set_xlabel("Time step")
    axes[1].set_ylabel("EQI (min delivery ratio)")
    axes[1].set_ylim(0, 1.1)
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(out, dpi=150)
    print(f"Saved comparison chart to {out}")
    plt.close()


def plot_bars(baseline_results, agent_results, out="results/utilization_bars.png"):
    b, a = pd.DataFrame(baseline_results), pd.DataFrame(agent_results)
    labels = ["Average worst-case\nutilization", "Peak worst-case\nutilization"]
    bv = [b["max_link_utilization_pct"].mean(), b["max_link_utilization_pct"].max()]
    av = [a["max_link_utilization_pct"].mean(), a["max_link_utilization_pct"].max()]
    x = range(2)
    plt.figure(figsize=(8, 5))
    plt.bar([i - 0.2 for i in x], bv, 0.4, label="Hop-count baseline")
    plt.bar([i + 0.2 for i in x], av, 0.4, label="Congestion-aware agent")
    plt.xticks(list(x), labels); plt.ylabel("Utilization (%)"); plt.legend()
    plt.tight_layout(); plt.savefig(out, dpi=150); plt.close()
    print(f"Saved bar chart to {out}")


def print_summary(baseline_results, agent_results):
    df_base = pd.DataFrame(baseline_results)
    df_agent = pd.DataFrame(agent_results)

    print("\n================ SUMMARY ================")
    print(f"{'Metric':40s} {'Baseline':>12s} {'Agent':>12s}")
    print(f"{'Avg worst-case link utilization (%)':40s} "
          f"{df_base['max_link_utilization_pct'].mean():12.1f} "
          f"{df_agent['max_link_utilization_pct'].mean():12.1f}")
    print(f"{'Avg EQI (fairness score)':40s} "
          f"{df_base['eqi'].mean():12.2f} "
          f"{df_agent['eqi'].mean():12.2f}")
    print(f"{'Time steps below equity floor':40s} "
          f"{(df_base['eqi'] < EQI_FLOOR).sum():12d} "
          f"{(df_agent['eqi'] < EQI_FLOOR).sum():12d}")
    print(f"{'Total reroutes (agent)':40s} {'-':>12s} {int(df_agent['reroutes'].sum()):12d}")
    print("===========================================\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--adaptive", action="store_true", help="rule-based adaptive weight")
    ap.add_argument("--weight", type=float, default=WEIGHT, help="congestion weight (default 10)")
    args, _ = ap.parse_known_args()
    tag = "_adaptive" if args.adaptive else ""

    traffic_series = generate_time_series(num_steps=NUM_STEPS)
    save_dataset(traffic_series)

    G_baseline = create_hospital_network()
    baseline_results = run_scenario(G_baseline, traffic_series, use_agent=False)
    save_results(baseline_results, "results/baseline_results.csv")

    G_agent = create_hospital_network()
    log_file = open_log(f"results/explainability_log{tag}.txt")
    agent_results = run_scenario(G_agent, traffic_series, use_agent=True, log_file=log_file,
                                 adaptive=args.adaptive, weight=args.weight)
    log_file.close()
    save_results(agent_results, f"results/agent_results{tag}.csv")

    plot_comparison(baseline_results, agent_results, out=f"results/comparison{tag}.png")
    plot_bars(baseline_results, agent_results, out=f"results/utilization_bars{tag}.png")
    print_summary(baseline_results, agent_results)
