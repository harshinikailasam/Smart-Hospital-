#!/usr/bin/env python3
"""
packet_sim.py -- PACKET-LEVEL discrete-event simulation (SimPy) of the hospital network.

Upgrade over simulate.py (flow-level): every packet is generated, queued, transmitted
and delivered or dropped, so we get real delay, jitter and loss per traffic class.

Model
  * same topology / link capacities / traffic series as the flow-level simulation
  * 1200-byte packets, exponential inter-arrival (Poisson) at the class's current demand
  * each directed link: transmission delay = size/capacity, 1 ms propagation, 200-packet
    drop-tail buffer, FIFO or STRICT-PRIORITY queuing (ICU > Telemedicine > IoT)
  * routing: baseline = hop-count shortest path (recomputed on topology change)
            agent    = congestion-weighted Dijkstra (routing.py) every 0.5 s using
                       link utilization MEASURED from bytes transmitted (0.25 s window);
                       a flow's own load is removed first and a switch needs >10% lower
                       cost (avoids oscillation); every switch is explained in plain English
  * optional: link failure (R2-R4 fails halfway), demand scaling (stress test), many seeds
  * EQI per 1 s step = min over classes of delivered/sent packets (from real loss)

Run:   python packet_sim.py                    # 4 configs x scale 1.0, 3 seeds
       python packet_sim.py --scales 1 1.5 2   # stress test
       python packet_sim.py --fail             # link failure at t = 10 s
"""
import argparse
import collections
import csv
import os
import random
import statistics as st

import simpy

import traffic
from topology import create_hospital_network
from routing import baseline_path, congestion_aware_path, path_cost, path_max_utilization_percent
from explainability import explain_decision
from equity import EQI_FLOOR

PKT, PROP, QLIM, STEP = 1200, 0.001, 200, 1.0      # bytes, seconds, packets, seconds
TYPES = traffic.TRAFFIC_TYPES
CLS = {n: i["priority"] for n, i in TYPES.items()}


class Pkt:
    __slots__ = ("name", "cls", "step", "t0", "path", "hop")

    def __init__(self, name, step, t0, path):
        self.name, self.cls, self.step, self.t0, self.path, self.hop = name, CLS[name], step, t0, path, 0


class Link:
    def __init__(self, env, net, cap, prio):
        self.env, self.net, self.cap, self.prio = env, net, cap, prio
        self.q = {c: collections.deque() for c in (1, 2, 3)}
        self.n = self.seq = self.bytes = 0
        self.busy = self.dead = False

    def send(self, pkt):
        if self.dead:
            return
        if self.n >= QLIM:                           # buffer full
            low = min((c for c, q in self.q.items() if q), default=None)
            if self.prio and low is not None and low < pkt.cls:
                self.q[low].pop()                    # priority mode: evict a lower-class packet
                self.n -= 1
            else:
                return                               # drop-tail
        self.seq += 1
        self.q[pkt.cls].append((self.seq, pkt))
        self.n += 1
        if not self.busy:
            self.busy = True
            self.env.process(self.run())

    def run(self):
        while self.n and not self.dead:
            heads = [(c, q[0]) for c, q in self.q.items() if q]
            c = max(h[0] for h in heads) if self.prio else min(heads, key=lambda h: h[1][0])[0]
            _, pkt = self.q[c].popleft()
            self.n -= 1
            yield self.env.timeout(PKT * 8 / (self.cap * 1e6))
            self.bytes += PKT
            self.env.process(self.arrive(pkt))
        self.busy = False

    def arrive(self, pkt):
        yield self.env.timeout(PROP)
        self.net.forward(pkt)

    def kill(self):
        self.dead = True
        for q in self.q.values():
            q.clear()
        self.n = 0


class Net:
    def __init__(self, env, G, prio, agent, scale, series, rnd, log):
        self.env, self.G, self.scale, self.series, self.rnd, self.log = env, G, scale, series, rnd, log
        self.links = {}
        for u, v, d in G.edges(data=True):
            for a, b in ((u, v), (v, u)):
                self.links[(a, b)] = Link(env, self, d["capacity"], prio)
        self.prev = {k: 0 for k in self.links}
        self.sent, self.got = collections.Counter(), collections.Counter()
        self.delays = {n: [] for n in CLS}
        self.reroutes = 0
        self.path = {n: baseline_path(G, i["source"], i["destination"]) for n, i in TYPES.items()}
        for n in CLS:
            env.process(self.gen(n))
        env.process(self.monitor())
        if agent:
            env.process(self.agent_loop())

    def gen(self, name):
        end = len(self.series) * STEP
        while True:
            step = min(int(self.env.now / STEP), len(self.series) - 1)
            rate = self.series[step][name] * self.scale
            yield self.env.timeout(self.rnd.expovariate(rate * 1e6 / (PKT * 8)))
            if self.env.now >= end:
                return
            step = int(self.env.now / STEP)
            self.sent[(name, step)] += 1
            self.forward(Pkt(name, step, self.env.now, self.path[name]))

    def forward(self, pkt):                          # packet is at path[pkt.hop]
        if pkt.hop == len(pkt.path) - 1:
            self.got[(pkt.name, pkt.step)] += 1
            self.delays[pkt.name].append(self.env.now - pkt.t0)
            return
        link = self.links.get((pkt.path[pkt.hop], pkt.path[pkt.hop + 1]))
        pkt.hop += 1
        if link is not None:
            link.send(pkt)

    def monitor(self, window=0.25):
        while True:
            yield self.env.timeout(window)
            for u, v in list(self.G.edges):
                rates = [(self.links[k].bytes - self.prev[k]) * 8 / window / 1e6 for k in ((u, v), (v, u))]
                self.G[u][v]["utilization"] = max(rates)
            for k, l in self.links.items():
                self.prev[k] = l.bytes

    def agent_loop(self, every=0.5):
        import contextlib, io
        while True:
            yield self.env.timeout(every)
            step = min(int(self.env.now / STEP), len(self.series) - 1)
            tmp = self.G.copy()                      # shared working view: later flows see earlier decisions
            for name, info in sorted(TYPES.items(), key=lambda kv: -kv[1]["priority"]):
                cur, own = self.path[name], self.series[step][name] * self.scale
                for a, b in zip(cur, cur[1:]):       # remove the flow's own load from what it sees
                    if tmp.has_edge(a, b):
                        tmp[a][b]["utilization"] = max(0, tmp[a][b]["utilization"] - own)
                new = congestion_aware_path(tmp, info["source"], info["destination"])
                if new != cur and path_cost(tmp, new) < 0.9 * path_cost(tmp, cur):
                    with contextlib.redirect_stdout(io.StringIO()):
                        explain_decision(round(self.env.now, 1), name, cur, new,
                                         path_max_utilization_percent(self.G, cur), self.log)
                    self.path[name] = cur = new
                    self.reroutes += 1
                for a, b in zip(cur, cur[1:]):       # commit the flow's load on its (new) path
                    if tmp.has_edge(a, b):
                        tmp[a][b]["utilization"] += own

    def fail(self, t, edge):
        yield self.env.timeout(t)
        u, v = edge
        self.G.remove_edge(u, v)
        self.links[(u, v)].kill()
        self.links[(v, u)].kill()
        for n, i in TYPES.items():
            self.path[n] = baseline_path(self.G, i["source"], i["destination"])

    def metrics(self, steps):
        eqis = [min(self.got[(n, t)] / self.sent[(n, t)] for n in CLS if self.sent[(n, t)]) for t in range(steps)]
        res = {"eqi_avg": st.mean(eqis), "eqi_min": min(eqis),
               "steps_below_floor": sum(e < EQI_FLOOR for e in eqis), "reroutes": self.reroutes}
        for n in CLS:
            sent = sum(self.sent[(n, t)] for t in range(steps))
            got = sum(self.got[(n, t)] for t in range(steps))
            d = sorted(x * 1000 for x in self.delays[n]) or [float("nan")]
            res[f"{n}_loss_pct"] = 100 * (1 - got / sent)
            res[f"{n}_delay_ms"] = st.mean(d)
            res[f"{n}_p95_ms"] = d[int(0.95 * (len(d) - 1))]
            raw = [x * 1000 for x in self.delays[n]]
            res[f"{n}_jitter_ms"] = st.mean(abs(a - b) for a, b in zip(raw, raw[1:])) if len(raw) > 1 else float("nan")
        return res


def run(seed, agent, prio, scale=1.0, fail=False, steps=20, log=None):
    random.seed(seed)
    series = traffic.generate_time_series(steps)
    env, G = simpy.Environment(), create_hospital_network()
    net = Net(env, G, prio, agent, scale, series, random.Random(seed), log)
    if fail:
        env.process(net.fail(steps * STEP / 2, ("R2", "R4")))
    env.run(until=steps * STEP + 1.0)
    return net.metrics(steps)


CONFIGS = [("Baseline + FIFO", False, False), ("Agent + FIFO", True, False),
           ("Baseline + Priority queue", False, True), ("Agent + Priority queue", True, True)]
COLS = [("ICU_delay_ms", "ICU delay ms"), ("ICU_p95_ms", "ICU p95 ms"), ("ICU_loss_pct", "ICU loss %"),
        ("TELEMEDICINE_loss_pct", "Tele loss %"), ("IOT_loss_pct", "IoT loss %"),
        ("eqi_avg", "EQI avg"), ("eqi_min", "EQI min"), ("steps_below_floor", "<floor"), ("reroutes", "reroutes")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--scales", type=float, nargs="+", default=[1.0])
    ap.add_argument("--fail", action="store_true", help="fail link R2-R4 at t=10 s")
    args = ap.parse_args()
    os.makedirs("results", exist_ok=True)
    log = open("results/packet_explainability_log.txt", "w")
    rows = []
    for scale in args.scales:
        print(f"\n=== demand scale x{scale} | link failure: {args.fail} | mean of {args.seeds} seeds ===")
        print(f"{'Configuration':28s}" + "".join(f"{lab:>13s}" for _, lab in COLS))
        for label, agent, prio in CONFIGS:
            runs = [run(s, agent, prio, scale, args.fail, log=log if (agent and s == 0) else None)
                    for s in range(args.seeds)]
            avg = {k: st.mean(r[k] for r in runs) for k, _ in COLS}
            sd = st.pstdev(r["ICU_p95_ms"] for r in runs)
            print(f"{label:28s}" + "".join(f"{avg[k]:13.2f}" for k, _ in COLS))
            rows.append({"scale": scale, "fail": args.fail, "config": label, "icu_p95_std_over_seeds": round(sd, 3),
                         **{k: round(v, 3) for k, v in avg.items()}})
    log.close()
    out = "results/packet_sim_summary" + ("_fail" if args.fail else "") + ("" if args.scales == [1.0] else "_stress") + ".csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"\nSaved {out} and results/packet_explainability_log.txt")


if __name__ == "__main__":
    main()
