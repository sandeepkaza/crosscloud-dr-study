"""Failure injection for the DR testbed (Paper 3).

Scenarios:
  F1  namespace loss     — delete namespace app on site A
  F2  pod loss (control) — delete writer pod only; site failover must NOT
                           trigger (self-heals in-cluster)
  F3  site loss          — docker stop <siteA node container> (full region/
                           provider outage from the orchestrator's view)
  F4  channel loss       — disconnect object store from network (backups
                           fail); heal after --heal-after seconds

Captures (last_seq, last_ts_ms) from the writer immediately before injection
and prints them as `PRE <seq> <ts>` — run_trials.py feeds these to
dr_orchestrator.py for RPO computation.

Usage:
  python inject.py --scenario F1|F2|F3|F4 --kubectl <path>
      [--site-container dra-control-plane] [--store-container minio]
      [--heal-after 120]
"""

import argparse
import subprocess
import sys
import time

CTX_A = "kind-dra"


def run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def pre_capture(kubectl):
    r = run([kubectl, "--context", CTX_A, "-n", "app", "exec", "deploy/writer",
             "--", "sh", "-c", "tail -n1 /data/log.txt"])
    parts = r.stdout.split()
    if r.returncode != 0 or len(parts) != 2:
        raise SystemExit(f"pre-capture failed: {r.stderr}")
    return int(parts[0]), int(parts[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True,
                    choices=["F1", "F2", "F3", "F4"])
    ap.add_argument("--kubectl", required=True)
    ap.add_argument("--site-container", default="dra-control-plane")
    ap.add_argument("--store-container", default="minio")
    ap.add_argument("--heal-after", type=int, default=120)
    args = ap.parse_args()

    seq, ts = pre_capture(args.kubectl)
    print(f"PRE {seq} {ts}", flush=True)

    if args.scenario == "F1":
        run([args.kubectl, "--context", CTX_A, "delete", "ns", "app",
             "--wait=false"])
    elif args.scenario == "F2":
        run([args.kubectl, "--context", CTX_A, "-n", "app", "delete", "pod",
             "-l", "app=writer", "--wait=false"])
    elif args.scenario == "F3":
        r = run(["docker", "stop", args.site_container], timeout=60)
        if r.returncode != 0:
            raise SystemExit(f"docker stop failed: {r.stderr}")
    elif args.scenario == "F4":
        run(["docker", "network", "disconnect", "kind", args.store_container])
        print(f"channel down; healing in {args.heal_after}s", flush=True)
        time.sleep(args.heal_after)
        run(["docker", "network", "connect", "kind", args.store_container])
        print("channel healed", flush=True)

    print(f"INJECTED {args.scenario}", flush=True)


if __name__ == "__main__":
    main()
