"""Minimal cross-site DR orchestrator (Paper 3 reference implementation).

Monitors site A (kind-dra) API-server liveness; on declared failure executes
the recovery runbook against site B (kind-drb):

  detect  -> N consecutive probe failures (PROBE_INTERVAL apart)
  locate  -> newest completed Velero backup visible at site B
  restore -> velero restore create --from-backup <latest> --wait
  ready   -> writer deployment Available AND restored data file non-empty

Per-phase monotonic timestamps -> one CSV row per trial (trials.csv):
  trial,scenario,detect_s,locate_s,restore_s,ready_s,rto_s,
  rpo_records,rpo_s,last_seq_pre,last_seq_post

RPO inputs (last_seq_pre = last sequence written at A, captured by inject.py
immediately before injection) are passed in via --pre-seq/--pre-ts.

Usage:
  python dr_orchestrator.py --trial 1 --scenario F3 --kubectl <path> \
      --velero <path> --pre-seq 1234 --pre-ts 1752790000000
"""

import argparse
import csv
import json
import os
import subprocess
import time

CTX_A = "kind-dra"   # overridden by --ctx-a
CTX_B = "kind-drb"   # overridden by --ctx-b
PROBE_INTERVAL = 2.0
PROBE_TIMEOUT = 3
FAILURES_TO_DECLARE = 3
OUT = "trials.csv"
BOUTIQUE = False     # --boutique: also wait for the frontend deployment


def run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def probe_a(kubectl):
    """Two-level probe: site (API /readyz) AND workload (writer available).

    A site-level failure (API down) and a workload-level failure (namespace or
    deployment gone/unavailable) both count as probe failure; the naive
    detector deliberately does not distinguish self-healing blips from real
    loss — F2 tests exactly that.
    """
    r = run([kubectl, "--context", CTX_A, "get", "--raw", "/readyz",
             f"--request-timeout={PROBE_TIMEOUT}s"], timeout=PROBE_TIMEOUT + 3)
    if r.returncode != 0:
        return False
    r = run([kubectl, "--context", CTX_A, "-n", "app", "get", "deploy",
             "writer", "-o", "jsonpath={.status.availableReplicas}",
             f"--request-timeout={PROBE_TIMEOUT}s"], timeout=PROBE_TIMEOUT + 3)
    return r.returncode == 0 and r.stdout.strip() == "1"


def wait_detect(kubectl):
    fails = 0
    while True:
        if probe_a(kubectl):
            fails = 0
        else:
            fails += 1
            if fails >= FAILURES_TO_DECLARE:
                return
        time.sleep(PROBE_INTERVAL)


def latest_backup(velero, kubectl):
    """Newest completed backup as seen from site B (BSL sync)."""
    deadline = time.time() + 300
    while time.time() < deadline:
        r = run([velero, "--kubecontext", CTX_B, "backup", "get", "-o", "json"])
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            items = data.get("items", data if isinstance(data, list) else [])
            done = [b for b in items
                    if b.get("status", {}).get("phase") == "Completed"]
            if done:
                done.sort(key=lambda b: b["status"]["completionTimestamp"])
                return done[-1]["metadata"]["name"]
        time.sleep(2)
    raise SystemExit("no completed backup visible at site B within 300s")


def restore(velero, name, trial):
    rname = f"dr-{trial}-{int(time.time())}"
    r = run([velero, "--kubecontext", CTX_B, "restore", "create", rname,
             "--from-backup", name, "--wait"], timeout=600)
    if r.returncode != 0:
        raise SystemExit(f"restore failed:\n{r.stderr}\n{r.stdout}")
    return rname


def wait_ready(kubectl):
    deadline = time.time() + 600
    while time.time() < deadline:
        r = run([kubectl, "--context", CTX_B, "-n", "app", "get", "deploy",
                 "writer", "-o",
                 "jsonpath={.status.availableReplicas}"])
        if r.stdout.strip() == "1":
            if BOUTIQUE:
                f = run([kubectl, "--context", CTX_B, "-n", "boutique",
                         "get", "deploy", "frontend", "-o",
                         "jsonpath={.status.availableReplicas}"])
                if f.stdout.strip() != "1":
                    time.sleep(2)
                    continue
            tail = data_tail(kubectl, CTX_B)
            if tail:
                return tail
        time.sleep(2)
    raise SystemExit("workload not ready on site B within 600s")


def data_tail(kubectl, ctx):
    """(last_seq, last_ts_ms) from the data file, or None."""
    r = run([kubectl, "--context", ctx, "-n", "app", "exec",
             "deploy/writer", "--", "sh", "-c",
             "tail -n1 /data/log.txt 2>/dev/null"])
    parts = r.stdout.split()
    if r.returncode == 0 and len(parts) == 2:
        return int(parts[0]), int(parts[1])
    return None


def restore_boundary(kubectl, pre_ts):
    """Last (seq, ts) in the restored file written at/before injection time.

    The resumed writer appends past the restored tail, so the plain tail
    overstates surviving data; the injection timestamp separates restored
    records from post-failover ones.
    """
    r = run([kubectl, "--context", CTX_B, "-n", "app", "exec",
             "deploy/writer", "--", "sh", "-c",
             f"awk -v t={pre_ts} '$2<=t {{s=$1; ts=$2}} "
             f"END{{print s, ts}}' /data/log.txt"])
    parts = r.stdout.split()
    if r.returncode == 0 and len(parts) == 2:
        return int(parts[0]), int(parts[1])
    return 0, 0


def main():
    global CTX_A, CTX_B, BOUTIQUE, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", type=int, required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--kubectl", required=True)
    ap.add_argument("--velero", required=True)
    ap.add_argument("--pre-seq", type=int, required=True)
    ap.add_argument("--pre-ts", type=int, required=True)
    ap.add_argument("--ctx-a", default=CTX_A)
    ap.add_argument("--ctx-b", default=CTX_B)
    ap.add_argument("--boutique", action="store_true")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    CTX_A, CTX_B, BOUTIQUE, OUT = args.ctx_a, args.ctx_b, args.boutique, args.out

    t0 = time.perf_counter()
    wait_detect(args.kubectl)
    t_detect = time.perf_counter()
    print(f"detected in {t_detect - t0:.1f}s", flush=True)

    bname = latest_backup(args.velero, args.kubectl)
    t_locate = time.perf_counter()
    print(f"latest backup: {bname} (+{t_locate - t_detect:.1f}s)", flush=True)

    restore(args.velero, bname, args.trial)
    t_restore = time.perf_counter()
    print(f"restore done (+{t_restore - t_locate:.1f}s)", flush=True)

    seq, ts = wait_ready(args.kubectl)
    t_ready = time.perf_counter()

    bseq, bts = restore_boundary(args.kubectl, args.pre_ts)
    rpo_records = max(args.pre_seq - bseq, 0)
    rpo_s = max((args.pre_ts - bts) / 1000.0, 0.0)

    row = {
        "trial": args.trial, "scenario": args.scenario,
        "detect_s": f"{t_detect - t0:.1f}",
        "locate_s": f"{t_locate - t_detect:.1f}",
        "restore_s": f"{t_restore - t_locate:.1f}",
        "ready_s": f"{t_ready - t_restore:.1f}",
        "rto_s": f"{t_ready - t0:.1f}",
        "rpo_records": rpo_records, "rpo_s": f"{rpo_s:.1f}",
        "last_seq_pre": args.pre_seq, "last_seq_post": seq,
    }
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    print(f"RTO {row['rto_s']}s  RPO {rpo_records} records / {row['rpo_s']}s",
          flush=True)


if __name__ == "__main__":
    main()
