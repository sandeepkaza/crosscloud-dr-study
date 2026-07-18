"""Trial lifecycle runner (Paper 3).

Per trial: heal environment -> wait steady state (writer up on A, a completed
backup NEWER than heal time visible at B) -> launch orchestrator -> inject ->
wait orchestrator verdict -> record.

F2 is the control: the orchestrator must NOT fail over; we record whether the
naive detector false-triggered (finding either way).

Usage:
  python run_trials.py --scenario F3 --trials 5 --kubectl <path> --velero <path>
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

CTX_A = "kind-dra"
CTX_B = "kind-drb"
APP_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "workload", "app.yaml")


def run(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


PV_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "workload", "local-pv.yaml")
NODES = {CTX_A: "dra-control-plane", CTX_B: "drb-control-plane"}


def pv_reset(kubectl, ctx):
    """Static local PV lifecycle: released PVs don't rebind — recreate clean."""
    node = NODES[ctx]
    run([kubectl, "--context", ctx, "delete", "pv", "appdata-local",
         "--ignore-not-found", "--wait=true"], timeout=120)
    run(["docker", "exec", node, "sh", "-c",
         "rm -rf /mnt/appdata && mkdir -p /mnt/appdata"], timeout=60)
    with open(PV_YAML, encoding="utf-8") as f:
        manifest = f.read().replace("NODE_NAME", node)
    subprocess.run([kubectl, "--context", ctx, "apply", "-f", "-"],
                   input=manifest, capture_output=True, text=True, timeout=60)


def heal(kubectl, scenario):
    # site A back up — regardless of the CURRENT scenario, the PREVIOUS
    # trial may have left the site container stopped
    run(["docker", "start", "dra-control-plane"], timeout=60)
    deadline = time.time() + 300
    while time.time() < deadline:
        r = run([kubectl, "--context", CTX_A, "get", "--raw", "/readyz",
                 "--request-timeout=3s"], timeout=10)
        if r.returncode == 0:
            break
        time.sleep(5)
    else:
        raise SystemExit("site A did not come back after docker start")
    # workload back on A (fresh if ns was lost); clean PV both sides first
    run([kubectl, "--context", CTX_A, "delete", "ns", "app",
         "--ignore-not-found", "--wait=true"], timeout=300)
    pv_reset(kubectl, CTX_A)
    run([kubectl, "--context", CTX_A, "apply", "-f", APP_YAML])
    deadline = time.time() + 300
    while time.time() < deadline:
        r = run([kubectl, "--context", CTX_A, "-n", "app", "get", "deploy",
                 "writer", "-o", "jsonpath={.status.availableReplicas}"])
        if r.stdout.strip() == "1":
            break
        time.sleep(5)
    else:
        raise SystemExit("writer did not become ready on A")
    # site B cleaned of any failed-over workload; fresh PV ready for restore
    run([kubectl, "--context", CTX_B, "delete", "ns", "app",
         "--ignore-not-found", "--wait=true"], timeout=300)
    pv_reset(kubectl, CTX_B)


def wait_fresh_backup(velero, after_iso):
    deadline = time.time() + 300
    while time.time() < deadline:
        r = run([velero, "--kubecontext", CTX_B, "backup", "get", "-o", "json"])
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            items = data.get("items", data if isinstance(data, list) else [])
            for b in items:
                st = b.get("status", {})
                if (st.get("phase") == "Completed"
                        and st.get("completionTimestamp", "") > after_iso):
                    return b["metadata"]["name"]
        time.sleep(5)
    raise SystemExit("no fresh completed backup after heal within 300s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True,
                    choices=["F1", "F2", "F3", "F4"])
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--kubectl", required=True)
    ap.add_argument("--velero", required=True)
    args = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))

    for trial in range(1, args.trials + 1):
        print(f"=== {args.scenario} trial {trial}: heal + steady state",
              flush=True)
        heal(args.kubectl, args.scenario)
        heal_iso = dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        fresh = wait_fresh_backup(args.velero, heal_iso)
        print(f"steady: fresh backup {fresh}", flush=True)

        inj = subprocess.run(
            [sys.executable, os.path.join(here, "inject.py"),
             "--scenario", args.scenario, "--kubectl", args.kubectl],
            capture_output=True, text=True, timeout=600)
        m = re.search(r"PRE (\d+) (\d+)", inj.stdout)
        if not m:
            raise SystemExit(f"inject failed:\n{inj.stdout}\n{inj.stderr}")
        pre_seq, pre_ts = m.group(1), m.group(2)
        print(f"injected ({args.scenario}); pre_seq={pre_seq}", flush=True)

        if args.scenario in ("F2", "F4"):
            # control scenarios: no failover expected. Give the detector a
            # window; verify writer still (or again) healthy on A.
            time.sleep(30)
            r = run([args.kubectl, "--context", CTX_A, "-n", "app", "get",
                     "deploy", "writer", "-o",
                     "jsonpath={.status.availableReplicas}"])
            ok = r.stdout.strip() == "1"
            with open("trials.csv", "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(
                    [trial, args.scenario, "", "", "", "", "",
                     "", "", pre_seq, "self-healed" if ok else "NOT-HEALED"])
            print(f"control outcome: {'self-healed' if ok else 'NOT healed'}",
                  flush=True)
            continue

        orch = subprocess.run(
            [sys.executable, os.path.join(here, "dr_orchestrator.py"),
             "--trial", str(trial), "--scenario", args.scenario,
             "--kubectl", args.kubectl, "--velero", args.velero,
             "--pre-seq", pre_seq, "--pre-ts", pre_ts],
            capture_output=True, text=True, timeout=1200)
        print(orch.stdout, flush=True)
        if orch.returncode != 0:
            raise SystemExit(f"orchestrator failed:\n{orch.stderr}")

    print("all trials done", flush=True)


if __name__ == "__main__":
    main()
