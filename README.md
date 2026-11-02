# Agentic, Explainable and Equity-Aware Congestion-Aware Routing for Smart Hospital Networks
Authors: Harshini Kailasam (24BCE0001), Diya Menon (24BAI0255) | Guide: Dr. Yoganand S, CFAIR, VIT

## Setup
    pip install networkx pandas matplotlib
    mkdir -p results

## Run
    python demo.py              # 4-stage live demo (Enter between stages); --fast skips pauses
    python simulate.py          # full 20-step experiment (reported results: W=10)
    python simulate.py --adaptive   # optional rule-based adaptive weight (outputs get _adaptive suffix)
    python simulate.py --weight 5   # try another fixed weight
    python verify_dijkstra.py   # cross-checks NetworkX against hand-written Dijkstra

## Files
topology.py (graph) | traffic.py (dataset) | routing.py (hop-count baseline, congestion-weighted Dijkstra, adaptive weight, manual Dijkstra) | explainability.py | equity.py (EQI) | simulate.py (evaluation)

## Algorithm
Baseline: unweighted Dijkstra (hop count). Agent: Dijkstra with cost(e) = 1 + W * utilization/capacity, flows routed ICU > Telemedicine > IoT.

## Packet-level version (Mininet) - Linux/VM only, needs root
    sudo apt install -y mininet iperf3 python3-networkx python3-pandas python3-matplotlib
    sudo python3 mininet_emulation.py --steps 6 --duration 8   # quick test (~2 min per mode)
    sudo python3 mininet_emulation.py                          # full: 20 steps, baseline then agent
    sudo mn -c                                                 # cleanup if a run crashed
Same topology/traffic/routing; flows are real UDP (iperf3) over tc-shaped links, utilization is
measured from interface counters, loss/jitter/RTT are measured. Outputs: results/mininet_*.csv,
mininet_comparison.png, mininet_explainability_log.txt

## Packet-level discrete-event simulation (tested) - works on any OS
    pip install simpy
    python packet_sim.py                    # 4 configs (baseline/agent x FIFO/priority queue), 3 seeds
    python packet_sim.py --scales 1.5 2     # stress test: 1.5x and 2x demand
    python packet_sim.py --fail             # link R2-R4 fails at t=10 s
Real per-packet queues, delay, jitter, loss per class, EQI from measured loss, priority queuing.
Outputs: results/packet_sim_summary*.csv, results/packet_explainability_log.txt
