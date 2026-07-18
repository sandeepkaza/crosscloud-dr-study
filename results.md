# Paper 3 results (measured; user validates every number before drafting)

All values client-observed, monotonic clock, from the harness in this folder.
RPO = writer sequence gap (last written seq − last restored seq), writer at
~5 records/s. Backup schedule: every 1 min (Velero, FSB/kopia, both rigs).

## Rig A — local emulation ($0 dev harness, comparison point)

kind v0.30.0 two clusters (dra/drb) + MinIO as S3, Docker Desktop 29.6.1
(WSL2), Windows 11. Velero 1.18.2. Workload: sequenced writer (busybox+PVC);
data: `trials.csv`, log `matrix.log`. 5 trials/scenario (F1/F3), controls ×3/×2.

| scenario | detect | locate | restore | ready | RTO median (range) | RPO median |
|---|---|---|---|---|---|---|
| F3 site loss | 4.5 | 0.2 | 9.0 | 2.6 | **16.2s** (14.1–16.7) | 29s |
| F1 ns loss | 12.0 | 0.2 | 8.8 | 2.6 | **23.7s** (22.7–24.6) | 32s |
| F2 pod loss (control) | — | — | — | — | self-healed ×3, no failover | — |
| F4 replication loss (control) | — | — | — | — | self-healed ×2, no failover | — |

## Rig B — real three-cloud rig (2026-07-17/18)

Site A = AKS `dr-aks` (2× Standard node, eastus, K8s 1.35.6);
site B = OKE `dr-oke` (2 nodes, K8s 1.36.1); site C = EKS `dr-eks`
(4× t3.micro, us-east-1, K8s 1.34.9). Replication channel = AWS S3 bucket
(`dr-paper3-velero-<AWS_ACCOUNT_ID>`), Velero 1.18.2 + FSB/kopia on all sites.
Workload: Online Boutique v0.10.6 (11 microservices + redis cart) + sequenced
writer; RTO gate = writer deployment ready on B (boutique restored in same
backup). Data: `trials_cloud_dr-oke.csv`, log `matrix-cloud.log`.

### AKS → OKE (3 valid trials per failover scenario)

| scenario | detect | locate | restore | ready | RTO median (range) | RPO median |
|---|---|---|---|---|---|---|
| F1c ns loss | 13.1 | 3.7 | 67.1 | 8.8 | **92.2s** (87.5–102.3) | 76s |
| F3c site loss (az aks stop) | 17.5 | 3.5 | 67.4 | 9.2 | **96.7s** (94.6–102.7) | 52s |
| F2c pod loss (control) | — | — | — | — | self-healed ×3, no failover | — |

Excluded: F1c trial 1 (RTO 583.8s) — stale-restore head-of-line blocking
induced by an orchestrator restart (ops-finding 12); replacement trial run.

### Decomposition shift, local → cloud

- Restore dominates on real clouds: 9s → ~67s (S3 WAN transfer + managed-CSI
  PV provisioning + 13-pod app vs 1-pod writer).
- locate_s grows 0.2s → ~3.5s (real S3 list + BSL sync vs LAN MinIO).
- detect_s roughly stable (probe-bound), ready_s grows with app size.
- Overall RTO ~4-6× local; ordering of scenarios preserved (F3 detect via
  API-death slower than F1 ns-watch on cloud, reversed vs local where F1
  detection was the slow path — detection design dominates, not the cloud).
- RPO stays cadence-bound (~0.5–1.4 min at 1-min schedule) on both rigs —
  replication channel, not cloud, sets RPO.

### AKS → EKS (3 trials per failover scenario; data `trials_cloud_dr-eks.csv`)

| scenario | detect | locate | restore | ready | RTO median (range) | RPO median |
|---|---|---|---|---|---|---|
| F1c ns loss | 13.8 | 2.3 | 20.0 | 10.6 | **47.1s** (40.2–47.3) | 62s |
| F3c site loss (az aks stop) | 17.9 | 2.3 | 23.0 | 7.6 | **53.0s** (50.3–54.8) | 41s |
| F2c pod loss (control) | — | — | — | — | self-healed ×3, no failover | — |

Notes: loadgenerator excluded from backups from this leg on
(velero.io/exclude-from-backup — non-essential load traffic; standard DR
scoping). A pre-matrix restore attempt with a 4-node recovery site timed out
(ops-finding 15, capacity artifact) and is not a trial.

### Recovery-site placement effect (AKS→EKS vs AKS→OKE)

Same source, same workload, same 1-min cadence: EKS (same region as the S3
replication bucket, us-east-1) recovers ~2× faster than OKE
(ap-hyderabad-1): F1c 47.1s vs 92.2s, F3c 53.0s vs 96.7s. The gap is almost
entirely restore_s (20–23s vs 67s) — object-store locality to the RECOVERY
site, not the source, dominates cross-cloud RTO. detect/locate/ready are
roughly constant across targets. RPO is cadence-bound on both (~0.5–1.1 min),
unaffected by placement.

## Portability defects encountered (candidate paper section)

See `ops-findings.md` — 13 numbered, log-backed findings (silent hostPath PV
skip, Windows-CLI manifest corruption, OKE short-name enforcement ×2,
nonexistent default restore-helper tag, IAM asymmetry, BSL sync lag,
storage-class non-portability, AWS free-plan instance gating, ENI maxPods
trap, EBS-CSI/IAM/default-SC triple trap on fresh EKS, stale-restore
head-of-line blocking, ARM-vs-API readiness race on AKS).
