"""Cross-checks NetworkX shortest_path against a hand-written Dijkstra on every flow
of every time step (congestion-weighted). Passes if total path costs are equal."""
from topology import create_hospital_network, reset_utilization, add_load
from traffic import TRAFFIC_TYPES, generate_time_series
from routing import congestion_aware_path, dijkstra_manual, path_cost
G = create_hospital_network(); n = 0
order = sorted(TRAFFIC_TYPES.values(), key=lambda i: -i["priority"])
for demand, in zip(generate_time_series(20),):
    reset_utilization(G)
    for (name, info) in sorted(TRAFFIC_TYPES.items(), key=lambda kv: -kv[1]["priority"]):
        s, d = info["source"], info["destination"]
        a, b = congestion_aware_path(G, s, d), dijkstra_manual(G, s, d)
        assert abs(path_cost(G, a) - path_cost(G, b)) < 1e-9, (name, a, b)
        add_load(G, a, demand[name]); n += 1
print(f"PASS: manual Dijkstra matches NetworkX cost on all {n} routing decisions")
