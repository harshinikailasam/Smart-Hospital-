"""
routing.py

This is the heart of the project: two ways of picking a path through the
network, so we can prove the second one is better.

1. baseline_path()
   Standard shortest-path routing (like RIP/OSPF) -- picks the path with
   the fewest hops, and never looks at how busy each link currently is.

2. congestion_aware_path()
   Our proposed metric:
       cost(link) = 1 + WEIGHT * (utilization / capacity)
   Then we pick the path with the lowest *total* cost, not just fewest
   hops. A congested link becomes "expensive" to use, so the router
   naturally prefers a longer-but-emptier path when one exists.

Both functions return a plain list of node names, e.g.
["ICU_Monitor", "ICU_Gateway", "R1", "R2", "R4", "Ward_Gateway"]
"""

import networkx as nx

# How strongly congestion should outweigh hop count.
# Bigger WEIGHT = router avoids congestion more aggressively,
# even if it means a longer path. This is the one "tunable knob"
# you'll want to experiment with and report on.
WEIGHT = 10


def baseline_path(G, source, destination):
    """Standard shortest-path routing: fewest hops, congestion-blind."""
    return nx.shortest_path(G, source=source, target=destination)


def _congestion_cost(edge_data, weight):
    """cost(e) = 1 + weight * (utilization / capacity)"""
    load_ratio = edge_data.get("utilization", 0) / edge_data.get("capacity", 1)
    return 1 + weight * load_ratio


def congestion_aware_path(G, source, destination, weight=None):
    """Congestion-weighted shortest path (Dijkstra via NetworkX).
    On an idle network every edge costs 1, so this equals hop-count routing."""
    w = WEIGHT if weight is None else weight
    return nx.shortest_path(
        G, source=source, target=destination,
        weight=lambda u, v, d: _congestion_cost(d, w)
    )


def adaptive_weight(prev_peak_util_pct, base=WEIGHT):
    """Optional RULE-BASED (non-learning) weight controller: react more
    strongly after a congested step, relax after a quiet one."""
    if prev_peak_util_pct >= 90:
        return base * 2
    if prev_peak_util_pct >= 70:
        return base
    return base / 2


def path_cost(G, path, weight=None):
    w = WEIGHT if weight is None else weight
    return sum(_congestion_cost(G[a][b], w) for a, b in zip(path, path[1:]))


def dijkstra_manual(G, source, destination, weight=None):
    """Hand-written Dijkstra (binary heap) using the same congestion cost.
    Used by verify_dijkstra.py to cross-check the NetworkX result."""
    import heapq
    w = WEIGHT if weight is None else weight
    dist, prev, heap = {source: 0.0}, {}, [(0.0, source)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float("inf")):
            continue
        if u == destination:
            break
        for v in G[u]:
            nd = d + _congestion_cost(G[u][v], w)
            if nd < dist.get(v, float("inf")):
                dist[v], prev[v] = nd, u
                heapq.heappush(heap, (nd, v))
    path, node = [destination], destination
    while node != source:
        node = prev[node]
        path.append(node)
    return path[::-1]


def path_max_utilization_percent(G, path):
    """Given a path, return the highest utilization% of any link on it.
    Useful for explainability: 'this path was chosen because the
    alternative was 85% full'."""
    worst = 0
    for i in range(len(path) - 1):
        data = G[path[i]][path[i + 1]]
        pct = (data["utilization"] / data["capacity"]) * 100
        worst = max(worst, pct)
    return worst


if __name__ == "__main__":
    from topology import create_hospital_network, add_load

    G = create_hospital_network()

    # Manually congest the R1-R2 link to see the agent react
    add_load(G, ["R1", "R2"], bandwidth=85)

    base = baseline_path(G, "ICU_Monitor", "Ward_Gateway")
    smart = congestion_aware_path(G, "ICU_Monitor", "Ward_Gateway")

    print("Baseline (hop-count) path:      ", base)
    print("Congestion-aware path:          ", smart)
    print("Max utilization on baseline path:", f"{path_max_utilization_percent(G, base):.0f}%")
    print("Max utilization on smart path:   ", f"{path_max_utilization_percent(G, smart):.0f}%")
