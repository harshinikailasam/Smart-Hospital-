"""
topology.py

Builds the smart-hospital network graph used throughout the project.

Devices:
    ICU_Monitor    -> ICU patient monitoring traffic
    Telemedicine   -> doctor/patient video calls
    IoT_Sensors    -> general ward IoT sensors (occupancy, equipment, etc.)

All three ultimately reach the "Ward_Gateway" node, which stands in for the
hospital's exit link to the rest of the network. The core (R1-R4) has more
than one path between most points on purpose -- that redundancy is what
lets a congestion-aware router actually have a choice to make.
"""

import networkx as nx
import matplotlib.pyplot as plt


def create_hospital_network():
    G = nx.Graph()

    # -------------------------
    # Hospital devices
    # -------------------------
    G.add_node("ICU_Monitor")
    G.add_node("Telemedicine")
    G.add_node("IoT_Sensors")

    # -------------------------
    # Hospital gateways
    # -------------------------
    G.add_node("ICU_Gateway")
    G.add_node("Ward_Gateway")
    G.add_node("Telemedicine_Gateway")

    # -------------------------
    # Core routers
    # -------------------------
    G.add_node("R1")
    G.add_node("R2")
    G.add_node("R3")
    G.add_node("R4")

    # -------------------------
    # Device -> gateway links
    # -------------------------
    G.add_edge("ICU_Monitor", "ICU_Gateway", capacity=100, utilization=0)
    G.add_edge("Telemedicine", "Telemedicine_Gateway", capacity=100, utilization=0)
    G.add_edge("IoT_Sensors", "Ward_Gateway", capacity=100, utilization=0)

    # -------------------------
    # Gateway -> core links
    # -------------------------
    G.add_edge("ICU_Gateway", "R1", capacity=100, utilization=0)
    G.add_edge("Telemedicine_Gateway", "R2", capacity=100, utilization=0)
    G.add_edge("Ward_Gateway", "R4", capacity=60, utilization=0)

    # -------------------------
    # Core mesh (deliberately redundant so there's more than one path).
    # Capacity is deliberately tighter here than at the device edges --
    # in a real hospital, the shared core/aggregation links are the
    # actual bottleneck, not each device's own connection. Without this
    # constraint, traffic demand never gets close to link capacity and
    # there's nothing for congestion-aware routing to actually solve.
    # -------------------------
    G.add_edge("R1", "R2", capacity=50, utilization=0)
    G.add_edge("R1", "R3", capacity=50, utilization=0)
    G.add_edge("R2", "R4", capacity=50, utilization=0)
    G.add_edge("R3", "R4", capacity=50, utilization=0)
    G.add_edge("R2", "R3", capacity=50, utilization=0)

    return G


def reset_utilization(G):
    """Zero out every link's utilization. Call this before routing a fresh
    time step so leftover traffic from the previous step doesn't linger."""
    for _, _, data in G.edges(data=True):
        data["utilization"] = 0


def add_load(G, path, bandwidth):
    """Add `bandwidth` Mbps of load to every link along `path`."""
    for i in range(len(path) - 1):
        G[path[i]][path[i + 1]]["utilization"] += bandwidth


def link_utilization_percent(G):
    """Return {(node1, node2): percent_utilized} for every link."""
    stats = {}
    for n1, n2, data in G.edges(data=True):
        stats[(n1, n2)] = (data["utilization"] / data["capacity"]) * 100
    return stats


def draw_network(G, save_path=None, title="Smart Hospital Network"):
    """Draw the topology with edge labels showing current utilization %.
    Pass save_path='results/topology.png' to save instead of just showing."""
    pos = nx.spring_layout(G, seed=42)
    plt.figure(figsize=(9, 6))

    nx.draw(
        G, pos, with_labels=True, node_color="lightblue",
        node_size=1800, font_size=8, font_weight="bold"
    )

    edge_labels = {}
    for n1, n2, data in G.edges(data=True):
        pct = (data["utilization"] / data["capacity"]) * 100
        edge_labels[(n1, n2)] = f"{pct:.0f}%"
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, font_size=7)

    plt.title(title)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150)
        print(f"Saved topology diagram to {save_path}")
    else:
        plt.show()
    plt.close()


if __name__ == "__main__":
    G = create_hospital_network()
    print("Hospital network created!")
    print("Number of nodes:", G.number_of_nodes())
    print("Number of links:", G.number_of_edges())

    print("\nNodes:")
    for node in G.nodes:
        print(" ", node)

    print("\nLinks:")
    for node1, node2, data in G.edges(data=True):
        print(f"  {node1} <-> {node2} | Capacity: {data['capacity']} Mbps")

    draw_network(G, save_path="results/topology.png")
