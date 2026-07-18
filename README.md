# Cross-Cloud Kubernetes Disaster Recovery Study

Companion repository for the paper **"Validated Failover: A Reference
Architecture and Three-Cloud Failure-Injection Evaluation of Kubernetes
Disaster Recovery"** (Venkata Sandeep Chowdary Kaza, 2026).

**Test your Kubernetes DR failover unattended.** A minimal DR orchestrator, a
failure-injection harness, and the complete raw data from — to our knowledge —
the first measured three-cloud managed-Kubernetes failover matrix: Azure AKS
failing over to Amazon EKS and Oracle OKE, carrying Google's 11-service Online
Boutique plus a sequenced writer that makes data loss (RPO) exactly countable.

## Headline results (medians)

| Path | Namespace loss (F1c) | Site loss (F3c) | RPO @ 1-min backups |
|---|---|---|---|
| local kind + MinIO | 23.7 s | 16.2 s | ~30 s |
| AKS → OKE (cross-region) | 92.2 s | 96.7 s | 52–76 s |
| AKS → EKS (bucket-local) | **47.1 s** | **53.0 s** | 41–62 s |

- **Placing the backup bucket near the recovery site halves RTO** — the
  restore phase dominates (20 s bucket-local vs 67 s cross-region).
- **RPO is bound by backup cadence everywhere** — placement moves RTO, not RPO.
- Control scenarios (pod kill, replication-channel loss) never caused a false failover.
- **15 reproducible portability findings** in [`ops-findings.md`](ops-findings.md) —
  silently empty backups, a nonexistent default restore-helper image tag, OKE
  short-name enforcement, storage classes that don't travel, ENI maxPods
  traps, cloud load balancers that outlive their cluster, and more.

## What's in this repo

| Path | What it is |
|---|---|
| `dr_orchestrator.py` | The runbook: detect failure → locate newest backup → restore → readiness gate, logging a timestamp at every phase boundary |
| `inject.py` | Failure injection for the local rig (namespace delete, pod kill, site stop, channel cut) |
| `run_trials.py` / `run_trials_cloud.py` | Per-trial protocol: heal site A → wait for a fresh backup → capture writer sequence → inject → measure |
| `run_matrix.ps1` / `run_matrix_cloud.ps1` | Unattended runners for the full scenario matrix |
| `cloud/` | AKS / EKS / OKE provisioning and teardown scripts |
| `workload/` | The sequenced-writer RPO instrument (`app.yaml` local, `app-cloud.yaml` managed clouds) |
| `sample-app/` | Vendored Online Boutique v0.10.6 manifests (Apache-2.0, Google) with fully-qualified images (finding 10) |
| `data/` | Every trial CSV and matrix log behind every number in the paper |
| `make_figures.py` | Regenerates the paper's figures from `data/*.csv` |
| `results.md` / `ops-findings.md` | Full results tables; the 15 findings with log citations |

## Reproducing — local rig (free, ~1 hour)

Prerequisites: Docker Desktop (or any Docker), `kind` v0.30+, `kubectl`,
`velero` CLI v1.18.x, Python 3.11+ (no packages beyond stdlib).

1. **Create two clusters** (A = primary, B = recovery):
   ```
   kind create cluster --name dra
   kind create cluster --name drb
   ```
2. **Start MinIO as the "cross-cloud" S3 channel** on the kind Docker network:
   ```
   docker run -d --name minio --network kind -p 9000:9000 \
     -e MINIO_ROOT_USER=minio -e MINIO_ROOT_PASSWORD=minio123 \
     quay.io/minio/minio server /data
   docker exec minio mkdir -p /data/velero
   ```
3. **Install Velero on BOTH clusters**, pointed at the same bucket
   (`--kubecontext kind-dra`, then `kind-drb`):
   ```
   velero install --kubecontext kind-dra --provider aws \
     --plugins velero/velero-plugin-for-aws:v1.14.0 --bucket velero \
     --secret-file ./credentials-velero \
     --backup-location-config region=minio,s3ForcePathStyle=true,s3Url=http://minio:9000 \
     --use-node-agent --default-volumes-to-fs-backup --uploader-type kopia
   ```
   where `credentials-velero` contains the MinIO keys in AWS format.
   **Windows note (finding 2):** the Windows velero CLI writes broken
   backslash hostPaths into the node-agent DaemonSet; patch
   `/var/lib/kubelet/pods` and `/var/lib/kubelet/plugins` back to POSIX.
4. **Deploy the workload on A** — `kubectl --context kind-dra apply -f workload/local-pv.yaml -f workload/app.yaml`.
   The writer appends `seq timestamp` lines to a PV at ~5 records/s; RPO is
   measured as the sequence gap after recovery. **Use local-type PVs, not the
   default hostPath provisioner** — Velero FSB silently skips hostPath volumes
   (finding 1: backups complete "successfully" with zero data).
5. **Create the backup schedule** on A:
   `velero --kubecontext kind-dra schedule create app-sched --schedule "*/1 * * * *" --include-namespaces app`
6. **Run the matrix**: `./run_matrix.ps1` (PowerShell) — or run scenarios
   individually:
   `python run_trials.py --scenario F3 --trials 5 --kubectl kubectl --velero velero`.
   Scenarios: `F1` namespace delete, `F2` pod kill (control — must NOT fail
   over), `F3` site loss (node container stopped), `F4` replication-channel
   cut (control).
7. **Read results**: `trials.csv` gains one row per trial with per-phase
   timings (`detect_s, locate_s, restore_s, ready_s, rto_s`) and the RPO
   columns. Compare with `data/trials.csv` from the paper's runs.

## Reproducing — three-cloud rig (~$20–30, one evening)

Prerequisites additionally: `az`, `aws` + `eksctl`, `oci` CLIs authenticated
to your own accounts; an S3 bucket for the shared backup storage location.

1. **Provision** with `cloud/provision.ps1` (AKS + the S3 bucket),
   `cloud/provision-eks.ps1`, `cloud/provision-oke.ps1` — fill in
   `<AWS_ACCOUNT_ID>` / `<TENANCY_OCID>` placeholders first. Small nodes are
   fine for the tooling but **size the recovery site for the full
   application** (finding 15: an undersized target leaves the restore
   `InProgress` forever). On EKS micro instances, enable VPC-CNI prefix
   delegation and raise max-pods (finding 11).
2. **Install Velero 1.18.x on all three clusters** against the same S3 bucket
   (same flags as local, `--backup-location-config region=us-east-1`), with an
   IAM user scoped to that bucket.
3. **Pre-provision the per-target ConfigMaps on every restore target**
   (both are mandatory — findings 7 and 9):
   - storage-class remap: map the source cloud's SC names to the target's
     (`change-storage-class-config`, label
     `velero.io/change-storage-class: RestoreItemAction`);
   - restore-helper image: point `restore-helper-config`
     (label `velero.io/pod-volume-restore: RestoreItemAction`) at
     `docker.io/velero/velero:<your-velero-version>` — the default
     `velero-restore-helper` tag does not exist for v1.15+.
4. **Deploy workloads on A**: `sample-app/online-boutique-v0.10.6.yaml` +
   `workload/app-cloud.yaml`; create the 1-minute schedule covering both
   namespaces. Keep every image fully qualified (finding 10: OKE rejects
   short names at restore time).
5. **Run the matrix per target**:
   `./run_matrix_cloud.ps1 -CtxB dr-eks` then `-CtxB dr-oke`
   (F1c namespace loss, F2c pod-kill control, F3c real site loss via
   `az aks stop` — each F3c trial stop/starts AKS, ~15 min each).
6. **Tear down the same day**: `eksctl delete cluster`, `az group delete`,
   `cloud/teardown-oke.ps1`. Check for orphaned load balancers created by
   restored `LoadBalancer` Services (finding 14: they outlive the cluster and
   keep billing).

## Data dictionary (`data/*.csv`)

`trial, scenario, detect_s, locate_s, restore_s, ready_s, rto_s,
rpo_records, rpo_s, last_seq_pre, last_seq_post` — one row per trial;
control scenarios carry `self-healed` in place of timings. `matrix*.log` are
the unabridged runner logs the findings cite.

## Citing

Use GitHub's **"Cite this repository"** button (backed by
[`CITATION.cff`](CITATION.cff)), or cite the paper directly once the preprint
DOI is live. License: MIT; vendored `sample-app/` manifests remain
Apache-2.0 (Google).
