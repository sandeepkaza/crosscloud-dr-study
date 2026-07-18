# Cross-Cloud Kubernetes Disaster Recovery Study

Companion repository for the paper **"Validated Failover: A Reference
Architecture and Three-Cloud Failure-Injection Evaluation of Kubernetes
Disaster Recovery"** (Venkata Sandeep Chowdary Kaza, 2026).

**Test your DR failover unattended.** This repo contains a minimal DR
orchestrator, a failure-injection harness, and the complete raw data from —
to our knowledge — the first measured three-cloud managed-Kubernetes failover
matrix: Azure AKS failing over to Amazon EKS and Oracle OKE, carrying Google's
11-service Online Boutique plus a sequenced writer that makes RPO exactly
countable.

## Headline results (medians)

| Path | Namespace loss (F1c) | Site loss (F3c) | RPO @ 1-min backups |
|---|---|---|---|
| local kind + MinIO | 23.7 s | 16.2 s | ~30 s |
| AKS → OKE (cross-region) | 92.2 s | 96.7 s | 52–76 s |
| AKS → EKS (bucket-local) | **47.1 s** | **53.0 s** | 41–62 s |

- **Object-store locality to the recovery site halves RTO** — the restore
  phase dominates (20 s bucket-local vs 67 s cross-region).
- **RPO is cadence-bound everywhere** — placement moves RTO, not RPO.
- Controls (pod kill, replication-channel loss) never triggered false failover.
- **15 log-backed portability findings** in [`ops-findings.md`](ops-findings.md):
  silently empty backups, a nonexistent default restore-helper image tag,
  OKE short-name enforcement, storage-class non-portability, ENI maxPods traps,
  restores that outlive clusters, and more. Each reproduces from this repo.

## Layout

- `dr_orchestrator.py` — the runbook: detect → locate newest backup → restore → gate; per-phase monotonic timestamps
- `inject.py`, `run_trials.py`, `run_trials_cloud.py` — failure matrix + heal/steady-state trial protocol
- `run_matrix.ps1`, `run_matrix_cloud.ps1` — unattended matrix runners
- `cloud/` — AKS/EKS/OKE provisioning + teardown scripts (fill in `<AWS_ACCOUNT_ID>` / `<TENANCY_OCID>`)
- `workload/` — sequenced-writer RPO instrument; `sample-app/` — vendored Online Boutique v0.10.6 (Apache-2.0, Google)
- `data/` — every trial CSV and matrix log behind every number in the paper
- `make_figures.py`, `make_arch_diagram.py`, `figures/` — regenerate the paper's figures from the CSVs
- `results.md` — full results; `ops-findings.md` — the 15 findings with log citations

## Reproducing

Local rig ($0): two kind clusters + MinIO + Velero 1.18.2 (FSB/kopia), then
`run_matrix.ps1`. Cloud rig: provision with `cloud/*.ps1`, install Velero on
each cluster against one S3 bucket, apply the per-target ConfigMaps the paper
describes (storage-class remap + restore-helper image), then
`run_matrix_cloud.ps1 -CtxB <context>`. Tear down the same day; the paper's
whole cloud matrix ran overnight on 2–7 small nodes per cluster.

## Citing

See [`CITATION.cff`](CITATION.cff) (GitHub's "Cite this repository" button).
License: MIT (vendored `sample-app/` manifests remain Apache-2.0, Google).
