"""Cloud trial runner (Paper 3): real managed clusters, real app.

Site A = AKS (dr-aks, rg dr-paper3); site B = --ctx-b (dr-eks | dr-oke).
Workload = Online Boutique (ns boutique) + sequenced writer (ns app).
Velero schedule on A backs up both namespaces every minute to AWS S3.

Scenarios:
  F3c  site loss    — az aks stop (control plane + nodes) --no-wait
  F1c  ns loss      — delete namespaces app+boutique on A
  F2c  pod loss     — delete writer pod (control: no failover)

Output: trials_cloud.csv (adds ctx_b column via --out naming per path).

Usage:
  python run_trials_cloud.py --scenario F3c --trials 3 \
      --ctx-b dr-eks --kubectl <path> --velero <path>
"""

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time

AZ = shutil.which("az") or shutil.which("az.cmd") or "az"

CTX_A = "dr-aks"
RG = "dr-paper3"
AKS_NAME = "dr-aks"
HERE = os.path.dirname(os.path.abspath(__file__))
APP_YAML = os.path.join(HERE, "workload", "app-cloud.yaml")
BOUTIQUE_YAML = os.path.join(HERE, "sample-app", "online-boutique-v0.10.6.yaml")


def run(cmd, timeout=600, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, **kw)


def az(args, timeout=900):
    return run([AZ] + args, timeout=timeout)


def api_ready(kubectl, ctx):
    r = run([kubectl, "--context", ctx, "get", "--raw", "/readyz",
             "--request-timeout=3s"], timeout=10)
    return r.returncode == 0


def wait_deploy(kubectl, ctx, ns, name, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = run([kubectl, "--context", ctx, "-n", ns, "get", "deploy", name,
                 "-o", "jsonpath={.status.availableReplicas}"])
        if r.stdout.strip() == "1":
            return True
        time.sleep(10)
    return False


def heal(kubectl, velero, ctx_b):
    # A powered on
    ps = az(["aks", "show", "-g", RG, "-n", AKS_NAME,
             "--query", "powerState.code", "-o", "tsv"])
    if "Stopped" in ps.stdout:
        print("starting AKS...", flush=True)
        for attempt in range(10):
            r = az(["aks", "start", "-g", RG, "-n", AKS_NAME], timeout=1800)
            if r.returncode == 0:
                break
            if "OperationNotAllowed" not in r.stderr:
                raise SystemExit(f"aks start failed: {r.stderr[:300]}")
            time.sleep(60)
        else:
            raise SystemExit("aks start: OperationNotAllowed persisted")
    deadline = time.time() + 900
    while time.time() < deadline:
        if api_ready(kubectl, CTX_A):
            break
        time.sleep(10)
    else:
        raise SystemExit("AKS API not ready after start")

    # ops-finding 13: API readiness precedes ARM operation completion —
    # a stop issued while the start operation is still "InProgress" is
    # rejected with OperationNotAllowed. Wait for provisioningState.
    deadline = time.time() + 1200
    while time.time() < deadline:
        ps = az(["aks", "show", "-g", RG, "-n", AKS_NAME,
                 "--query", "provisioningState", "-o", "tsv"])
        if ps.stdout.strip() == "Succeeded":
            break
        time.sleep(20)
    else:
        raise SystemExit("AKS provisioningState never reached Succeeded")

    # workloads on A
    run([kubectl, "--context", CTX_A, "create", "ns", "boutique"])
    r = run([kubectl, "--context", CTX_A, "apply", "-n", "boutique",
             "-f", BOUTIQUE_YAML], timeout=300)
    if r.returncode != 0:
        raise SystemExit(f"boutique apply failed: {r.stderr[:300]}")
    run([kubectl, "--context", CTX_A, "apply", "-f", APP_YAML], timeout=300)
    if not (wait_deploy(kubectl, CTX_A, "app", "writer")
            and wait_deploy(kubectl, CTX_A, "boutique", "frontend")):
        raise SystemExit("workloads not ready on A")

    # B clean
    for ns in ("app", "boutique"):
        run([kubectl, "--context", ctx_b, "delete", "ns", ns,
             "--ignore-not-found", "--wait=true"], timeout=600)


def ensure_schedule(velero):
    r = run([velero, "--kubecontext", CTX_A, "schedule", "get", "app-sched"])
    if r.returncode != 0:
        run([velero, "--kubecontext", CTX_A, "schedule", "create", "app-sched",
             "--schedule", "*/1 * * * *",
             "--include-namespaces", "app,boutique"])


def wait_fresh_backup(velero, ctx_b, after_iso):
    deadline = time.time() + 600
    while time.time() < deadline:
        r = run([velero, "--kubecontext", ctx_b, "backup", "get", "-o", "json"])
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            items = data.get("items", data if isinstance(data, list) else [])
            for b in items:
                st = b.get("status", {})
                if (st.get("phase") == "Completed"
                        and st.get("completionTimestamp", "") > after_iso):
                    return b["metadata"]["name"]
        time.sleep(10)
    raise SystemExit("no fresh completed backup after heal within 600s")


def pre_capture(kubectl):
    r = run([kubectl, "--context", CTX_A, "-n", "app", "exec", "deploy/writer",
             "--", "sh", "-c", "tail -n1 /data/log.txt"])
    parts = r.stdout.split()
    if r.returncode != 0 or len(parts) != 2:
        raise SystemExit(f"pre-capture failed: {r.stderr[:200]}")
    return parts[0], parts[1]


def inject(kubectl, scenario):
    if scenario == "F3c":
        for attempt in range(5):
            r = az(["aks", "stop", "-g", RG, "-n", AKS_NAME, "--no-wait"])
            if r.returncode == 0:
                break
            if "OperationNotAllowed" not in r.stderr:
                raise SystemExit(f"aks stop failed: {r.stderr[:300]}")
            time.sleep(30)
        else:
            raise SystemExit(f"aks stop failed after retries: {r.stderr[:300]}")
    elif scenario == "F1c":
        run([kubectl, "--context", CTX_A, "delete", "ns", "app", "boutique",
             "--wait=false"])
    elif scenario == "F2c":
        run([kubectl, "--context", CTX_A, "-n", "app", "delete", "pod",
             "-l", "app=writer", "--wait=false"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True,
                    choices=["F3c", "F1c", "F2c"])
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--ctx-b", required=True)
    ap.add_argument("--kubectl", required=True)
    ap.add_argument("--velero", required=True)
    args = ap.parse_args()
    out = f"trials_cloud_{args.ctx_b}.csv"

    ensure_schedule(args.velero)
    for trial in range(1, args.trials + 1):
        print(f"=== {args.scenario} trial {trial} (B={args.ctx_b}): heal",
              flush=True)
        heal(args.kubectl, args.velero, args.ctx_b)
        heal_iso = dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        fresh = wait_fresh_backup(args.velero, args.ctx_b, heal_iso)
        print(f"steady: fresh backup {fresh}", flush=True)

        pre_seq, pre_ts = pre_capture(args.kubectl)
        inject(args.kubectl, args.scenario)
        print(f"injected {args.scenario}; pre_seq={pre_seq}", flush=True)

        if args.scenario == "F2c":
            time.sleep(45)
            ok = wait_deploy(args.kubectl, CTX_A, "app", "writer", 120)
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"{trial},F2c,,,,,,,,{pre_seq},"
                        f"{'self-healed' if ok else 'NOT-HEALED'}\n")
            continue

        orch = subprocess.run(
            [sys.executable, os.path.join(HERE, "dr_orchestrator.py"),
             "--trial", str(trial), "--scenario", args.scenario,
             "--kubectl", args.kubectl, "--velero", args.velero,
             "--pre-seq", pre_seq, "--pre-ts", pre_ts,
             "--ctx-a", CTX_A, "--ctx-b", args.ctx_b,
             "--boutique", "--out", out],
            capture_output=True, text=True, timeout=2400)
        print(orch.stdout, flush=True)
        if orch.returncode != 0:
            raise SystemExit(f"orchestrator failed:\n{orch.stderr}")

    print("cloud trials done", flush=True)


if __name__ == "__main__":
    main()
