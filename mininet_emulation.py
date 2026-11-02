#!/usr/bin/env python3
"""
mininet_emulation.py  --  packet-level version of the project (Mininet + iperf3 + ping)

Same topology, same traffic series (seed 42) and same routing logic as the
Python simulation, but flows are REAL UDP packets (iperf3) crossing Linux
routers whose links are shaped by tc to the link capacities.

How routing works (no SDN controller needed):
  * every node (R1..R4, gateways, devices) is a Mininet host with IP forwarding on
  * the routing agent (this script) installs per-flow /32 routes with `ip route`
  * baseline  : static hop-count path (nx.shortest_path)
  * agent     : congestion-weighted Dijkstra (routing.py) using utilization
                MEASURED from interface byte counters (`ip -s link`)
  * flows are started in priority order (ICU, Telemedicine, IoT); before routing
    each flow the agent observes the load of the flows already running

Measured per step: loss %, jitter, RTT (ping), delivery ratio -> EQI, reroutes.

Run (Ubuntu/Linux VM, needs root):
    sudo apt install -y mininet iperf3 python3-networkx python3-pandas python3-matplotlib
    sudo python3 mininet_emulation.py --steps 6 --duration 8     # quick test
    sudo python3 mininet_emulation.py                            # full: 20 steps, both modes
    sudo mn -c                                                   # clean up after a crash
"""
import argparse
import csv
import json
import os
import re
import sys
import time

from topology import create_hospital_network, add_load
from traffic import TRAFFIC_TYPES, generate_time_series
from routing import baseline_path, congestion_aware_path, path_max_utilization_percent
from explainability import explain_decision
from equity import compute_eqi, check_equity_floor, EQI_FLOOR

# Linux interface names are limited to 15 chars, so use short host names.
SHORT = {"ICU_Monitor": "icum", "ICU_Gateway": "icug", "Telemedicine": "tele",
         "Telemedicine_Gateway": "telg", "IoT_Sensors": "iot", "Ward_Gateway": "ward",
         "R1": "r1", "R2": "r2", "R3": "r3", "R4": "r4"}

# One (source-loopback, destination-loopback, iperf port) per traffic class, so each
# flow can have its own /32 route even though all flows end at Ward_Gateway.
FLOW_ADDR = {name: (f"10.254.0.{i + 1}", f"10.255.0.{i + 1}", 5201 + i)
             for i, name in enumerate(TRAFFIC_TYPES)}


# ---------------------------------------------------------------- pure helpers
def route_commands(path, src_ip, dst_ip, ip, ifn):
    """List of (node, shell command) that install forward and reverse /32 routes
    along `path`. ip[(a, b)] = a's address on its link to b; ifn[(a, b)] = a's
    interface name on that link."""
    cmds = []
    for a, b in zip(path, path[1:]):                      # forward: toward dst
        cmds.append((a, f"ip route replace {dst_ip}/32 via {ip[(b, a)]} dev {ifn[(a, b)]}"))
    for a, b in zip(path[1:], path):                      # reverse: toward src
        cmds.append((a, f"ip route replace {src_ip}/32 via {ip[(b, a)]} dev {ifn[(a, b)]}"))
    return cmds


def parse_iperf_udp(text):
    """Parse iperf3 -u -J client output -> dict or None."""
    try:
        data = json.JSONDecoder().raw_decode(text[text.index("{"):])[0]
        s = data["end"]["sum"]
        return {"loss_pct": float(s.get("lost_percent", 0.0)),
                "jitter_ms": float(s.get("jitter_ms", 0.0)),
                "mbps": float(s["bits_per_second"]) / 1e6}
    except Exception:
        return None


def parse_ping_avg(text):
    m = re.search(r"= [\d.]+/([\d.]+)/", text)
    return float(m.group(1)) if m else None


# ------------------------------------------------------------------- emulator
class Emu:
    def __init__(self, G):
        from mininet.net import Mininet
        from mininet.link import TCLink
        self.G = G
        self.net = Mininet(controller=None, link=TCLink, autoSetMacs=True)
        self.h = {n: self.net.addHost(SHORT[n]) for n in G.nodes}
        self.ip, self.ifn = {}, {}
        for k, (u, v, d) in enumerate(G.edges(data=True)):
            a, b = f"10.0.{k}.1", f"10.0.{k}.2"
            link = self.net.addLink(self.h[u], self.h[v], bw=d["capacity"],
                                    params1={"ip": a + "/30"}, params2={"ip": b + "/30"})
            self.ip[(u, v)], self.ip[(v, u)] = a, b
            self.ifn[(u, v)], self.ifn[(v, u)] = link.intf1.name, link.intf2.name
        self.net.start()
        for h in self.h.values():
            h.cmd("sysctl -w net.ipv4.ip_forward=1 net.ipv4.conf.all.rp_filter=0 "
                  "net.ipv4.conf.default.rp_filter=0")
            for intf in h.intfList():
                h.cmd(f"sysctl -w net.ipv4.conf.{intf.name}.rp_filter=0")
        for name, info in TRAFFIC_TYPES.items():
            sip, dip, _ = FLOW_ADDR[name]
            self.h[info["source"]].cmd(f"ip addr add {sip}/32 dev lo")
            self.h[info["destination"]].cmd(f"ip addr add {dip}/32 dev lo")

    def stop(self):
        self.net.stop()

    # --- monitoring -------------------------------------------------------
    def _tx(self, node, ifname):
        out = self.h[node].cmd(f"ip -s -j link show dev {ifname}")
        try:
            return int(json.JSONDecoder().raw_decode(out[out.index("["):])[0][0]["stats64"]["tx"]["bytes"])
        except Exception:
            txt = self.h[node].cmd(f"ip -s link show dev {ifname}")
            m = re.search(r"TX:.*\n\s*(\d+)", txt)
            return int(m.group(1)) if m else 0

    def measure_util(self, window=1.0):
        """Measure per-link throughput (Mbps) over `window` s and store it as
        the link's 'utilization' (max of the two directions)."""
        G = self.G
        before = {(u, v): (self._tx(u, self.ifn[(u, v)]), self._tx(v, self.ifn[(v, u)]))
                  for u, v in G.edges}
        t0 = time.time()
        time.sleep(window)
        dt = time.time() - t0
        for u, v in G.edges:
            a, b = self._tx(u, self.ifn[(u, v)]), self._tx(v, self.ifn[(v, u)])
            G[u][v]["utilization"] = max(a - before[(u, v)][0], b - before[(u, v)][1]) * 8 / dt / 1e6

    def install(self, path, name):
        sip, dip, _ = FLOW_ADDR[name]
        for node, cmd in route_commands(path, sip, dip, self.ip, self.ifn):
            self.h[node].cmd(cmd)

    # --- one time step ----------------------------------------------------
    def run_step(self, t, demand, agent, duration, settle, log):
        G = self.G
        for _, _, d in G.edges(data=True):
            d["utilization"] = 0
        offered = create_hospital_network()          # analytic offered load (same model as simulation)
        flows = sorted(TRAFFIC_TYPES.items(), key=lambda kv: -kv[1]["priority"])
        t_start, reroutes, jobs = time.time(), 0, []

        for name, info in flows:
            src, dst, bw = info["source"], info["destination"], demand[name]
            base = baseline_path(G, src, dst)
            if agent:
                if jobs:
                    self.measure_util(1.0)           # observe load of flows already running
                chosen = congestion_aware_path(G, src, dst)
                if chosen != base:
                    reroutes += 1
                    explain_decision(t, name, base, chosen, path_max_utilization_percent(G, base), log)
            else:
                chosen = base
                if jobs:
                    time.sleep(1.0)                  # same timing as the agent run
            self.install(chosen, name)
            add_load(offered, chosen, bw)

            sip, dip, port = FLOW_ADDR[name]
            remaining = max(3, int(duration - (time.time() - t_start)))
            tag = f"/tmp/cn_{name}_{t}"
            self.h[dst].cmd(f"iperf3 -s -B {dip} -p {port} -1 >/dev/null 2>&1 &")
            time.sleep(0.3)
            self.h[src].cmd(f"iperf3 -c {dip} -B {sip} -p {port} -u -b {bw}M -l 1200 "
                            f"-t {remaining} -J > {tag}.json 2>&1 &")
            self.h[src].cmd(f"ping -c {max(5, int((remaining - 1) / 0.2))} -i 0.2 -I {sip} {dip} "
                            f"> {tag}.ping 2>&1 &")
            jobs.append((name, src, tag))
            time.sleep(settle)

        self.measure_util(1.0)                       # all flows running: measured peak
        measured_peak = max(d["utilization"] / d["capacity"] for _, _, d in G.edges(data=True)) * 100
        offered_peak = max(d["utilization"] / d["capacity"] for _, _, d in offered.edges(data=True)) * 100
        time.sleep(max(0, t_start + duration + 2.5 - time.time()))

        ratios, row = {}, {"time_step": t}
        for name, src, tag in jobs:
            res = parse_iperf_udp(self.h[src].cmd(f"cat {tag}.json"))
            rtt = parse_ping_avg(self.h[src].cmd(f"cat {tag}.ping"))
            self.h[src].cmd(f"rm -f {tag}.json {tag}.ping")
            if res is None:
                print(f"  [warn] t={t} {name}: could not parse iperf3 output (treated as 0 delivered)")
                res = {"loss_pct": 100.0, "jitter_ms": float("nan"), "mbps": 0.0}
            ratios[name] = max(0.0, min(1.0, 1 - res["loss_pct"] / 100))
            row[f"loss_pct_{name}"] = round(res["loss_pct"], 2)
            row[f"jitter_ms_{name}"] = round(res["jitter_ms"], 3)
            row[f"rtt_ms_{name}"] = round(rtt, 2) if rtt is not None else ""
            row[f"delivery_{name}"] = round(ratios[name], 4)
        starved = check_equity_floor(ratios)
        row.update({"eqi": round(compute_eqi(ratios), 4), "reroutes": reroutes,
                    "offered_max_util_pct": round(offered_peak, 1),
                    "measured_max_util_pct": round(measured_peak, 1),
                    "starved_traffic": ",".join(starved) if starved else "none"})
        return row


# --------------------------------------------------------------------- output
def save_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {path}")


def plot(base, agent, out="results/mininet_comparison.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    b, a = pd.DataFrame(base), pd.DataFrame(agent)
    fig, ax = plt.subplots(3, 1, figsize=(10, 10))
    ax[0].plot(b.time_step, b.offered_max_util_pct, "o-", label="Baseline")
    ax[0].plot(a.time_step, a.offered_max_util_pct, "o-", label="Agent")
    ax[0].axhline(100, color="red", ls="--"); ax[0].set_title("Offered worst-case link utilization (%)"); ax[0].legend()
    ax[1].plot(b.time_step, b.eqi, "o-", label="Baseline"); ax[1].plot(a.time_step, a.eqi, "o-", label="Agent")
    ax[1].axhline(EQI_FLOOR, color="red", ls="--"); ax[1].set_ylim(0, 1.1)
    ax[1].set_title("EQI from measured packet loss (min delivery ratio)"); ax[1].legend()
    for lab, df in (("Baseline", b), ("Agent", a)):
        ax[2].plot(df.time_step, pd.to_numeric(df.rtt_ms_ICU, errors="coerce"), "o-", label=lab)
    ax[2].set_title("ICU round-trip time under load (ms, ping)"); ax[2].set_xlabel("Time step"); ax[2].legend()
    plt.tight_layout(); plt.savefig(out, dpi=150); plt.close()
    print(f"Saved {out}")


def summary(base, agent):
    import pandas as pd
    b, a = pd.DataFrame(base), pd.DataFrame(agent)
    lossmean = lambda d: d[[c for c in d if c.startswith("loss_pct_")]].mean().mean()
    print("\n=========== MININET SUMMARY ===========")
    print(f"{'Metric':42s}{'Baseline':>10s}{'Agent':>10s}")
    for label, f in [("Avg offered worst-case utilization (%)", lambda d: d.offered_max_util_pct.mean()),
                     ("Steps with offered load > 100%", lambda d: (d.offered_max_util_pct > 100).sum()),
                     ("Avg packet loss, all classes (%)", lossmean),
                     ("Avg EQI", lambda d: d.eqi.mean()),
                     ("Lowest EQI", lambda d: d.eqi.min()),
                     ("Steps with EQI below floor", lambda d: (d.eqi < EQI_FLOOR).sum()),
                     ("Avg ICU RTT (ms)", lambda d: pd.to_numeric(d.rtt_ms_ICU, errors="coerce").mean()),
                     ("Total reroutes", lambda d: d.reroutes.sum())]:
        print(f"{label:42s}{f(b):10.2f}{f(a):10.2f}")
    print("=======================================\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--duration", type=int, default=10, help="seconds per time step")
    ap.add_argument("--mode", choices=["both", "baseline", "agent"], default="both")
    args = ap.parse_args()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        sys.exit("Run on Linux with root:  sudo python3 mininet_emulation.py")
    from mininet.log import setLogLevel
    setLogLevel("warning")
    os.makedirs("results", exist_ok=True)

    series = generate_time_series(args.steps)        # identical to the simulation's first N steps
    emu = Emu(create_hospital_network())
    out = {}
    try:
        for mode in (["baseline", "agent"] if args.mode == "both" else [args.mode]):
            print(f"\n--- running {mode} ({args.steps} steps x {args.duration}s) ---")
            log = open("results/mininet_explainability_log.txt", "w") if mode == "agent" else None
            rows = []
            for t, demand in enumerate(series):
                row = emu.run_step(t, demand, mode == "agent", args.duration, 1.0, log)
                rows.append(row)
                print(f"t={t:2d} EQI={row['eqi']:.2f} reroutes={row['reroutes']} "
                      f"offered={row['offered_max_util_pct']}% measured={row['measured_max_util_pct']}%")
            if log:
                log.close()
            save_csv(rows, f"results/mininet_{mode}.csv")
            out[mode] = rows
    finally:
        emu.stop()
    if len(out) == 2:
        plot(out["baseline"], out["agent"])
        summary(out["baseline"], out["agent"])


if __name__ == "__main__":
    main()
