"""demo.py - live demo. Run: python demo.py   (press Enter between stages; use --fast to skip pauses)"""
import sys
from topology import create_hospital_network, add_load, link_utilization_percent, draw_network
from routing import baseline_path, congestion_aware_path, path_max_utilization_percent
FAST = "--fast" in sys.argv
def stage(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)
    if not FAST: input("[Enter] ")
stage("STAGE 1: Smart-hospital topology (10 nodes, 3 traffic sources -> Ward_Gateway)")
G = create_hospital_network()
print("Nodes:", G.number_of_nodes(), "| Links:", G.number_of_edges())
draw_network(G, save_path="results/demo_idle.png", title="Idle network (0% load)")
stage("STAGE 2: Idle network - both routers agree")
src, dst = "Telemedicine", "Ward_Gateway"
print("Baseline :", " -> ".join(baseline_path(G, src, dst)))
print("Agent    :", " -> ".join(congestion_aware_path(G, src, dst)))
stage("STAGE 3: Inject congestion on R2-R4 (45 of 50 Mbps = 90%)")
add_load(G, ["R2", "R4"], 45)
b, a = baseline_path(G, src, dst), congestion_aware_path(G, src, dst)
print("Baseline (hop count, congestion-blind):", " -> ".join(b))
print("Agent    (congestion-aware)           :", " -> ".join(a))
print(f"Worst link on baseline path: {path_max_utilization_percent(G, b):.0f}%")
print(f"Worst link on agent path   : {path_max_utilization_percent(G, a):.0f}%")
print(f"\nEXPLANATION: Rerouted Telemedicine away from {' -> '.join(b)} (congestion {path_max_utilization_percent(G, b):.0f}%)")
print(f"             Used {' -> '.join(a)} instead to protect latency.")
draw_network(G, save_path="results/demo_congested.png", title="R2-R4 congested at 90%")
stage("STAGE 4: Full 20-step simulation (baseline vs agent + EQI fairness)")
import simulate
simulate.__name__ = "x"
import runpy; runpy.run_path("simulate.py", run_name="__main__")
print("\nOutputs in results/: comparison.png, explainability_log.txt, *_results.csv, traffic_dataset.csv")
