# Operational findings log (Paper 3, "portability footguns" section)

Every real defect hit while standing up the cross-cloud DR pipeline. Each is
reproducible and citable to logs in this folder.

1. **Velero FSB silently skips hostPath-backed PVs.** kind's default
   local-path provisioner creates hostPath PVs → backups "Completed, 0
   errors" while carrying zero PV data; restore produces an empty volume.
   Silent RPO=∞. (Local rig; velero log: "hostPath volume which is not
   supported for pod volume backup, skipping".) Mitigation: local-type PVs /
   CSI-backed storage; verify PodVolumeBackup objects exist, not just backup
   phase.
2. **Velero CLI on Windows emits broken node-agent manifests.** hostPath
   paths rendered with backslashes (`\var\lib\kubelet\pods`) → node-agent
   CrashLoopBackOff on every cluster (kind, AKS, OKE). Patched hostPath to
   POSIX paths post-install.
3. **Kyverno-style uninstall leftovers (Paper 2 crossover):** engine
   uninstall leaves dynamic webhook configs behind.
4. **OKE Oracle Linux enforces fully-qualified image references.** Velero's
   default `velero/velero` short names → ImageInspectError ("short name mode
   is enforcing"); AKS/EKS pull the same manifests fine. Cross-cloud DR
   restore implication: any restored workload with short image names breaks
   on OKE while running fine on the source cloud — a portability asymmetry
   invisible until failover day.
5. **IAM asymmetry:** minimal-permission CI identities (AWS user without
   eks/s3 grants) block the DR *tooling* even when app workloads run fine —
   DR runbooks need their own IAM budget, discovered at failover time
   otherwise.
6. **Velero backups are one-way visible:** BSL sync (site B seeing site A's
   backups) took up to ~1 min after first configuration — detection-to-restore
   pipelines must tolerate BSL sync lag (locate_s in our decomposition).

9. **Velero's default restore-helper image reference does not exist for
   v1.15+.** On FSB restore, the injected `restore-wait` init container
   defaulted to `velero/velero-restore-helper:v1.18.2` — a tag that is not
   published (helper was folded into the main `velero/velero` image in
   v1.15). Every stateful pod restore hung in `Init:ErrImagePull`
   ("manifest unknown") on OKE. Fix: `restore-helper-config` ConfigMap
   pointing at `docker.io/velero/velero:v1.18.2` — required on EVERY restore
   target cluster, discovered only at restore time.
10. **Short image names break twice on OKE.** Beyond Velero's own images
   (finding 4), the *workload's* short names (`redis:alpine`,
   `busybox:1.36`) restore fine on AKS/EKS but hit OKE's short-name
   enforcement. Mitigation: fully-qualify every image in the source
   manifests so backups carry portable references.
11. **A fresh EKS recovery site cannot provision PVs out of the box.**
   (a) t3.micro ENI limit caps maxPods at 4/node — system daemonsets consume
   nearly all slots; required VPC-CNI prefix delegation + nodegroup rebuilt
   with `--max-pods-per-node 25`. (b) K8s 1.34 removed the in-tree EBS
   provisioner; default `gp2` SC is inert until the aws-ebs-csi-driver addon
   is installed WITH IAM (AmazonEBSCSIDriverPolicy on the node role). (c) gp2
   is not annotated as default SC, so SC-less PVCs pend. Three independent
   failover-day surprises on the recovery cloud.
12. **A killed/interrupted restore head-of-line blocks all later restores.**
   An orchestrator restart left a Velero restore `InProgress` whose target
   pods no longer existed; its PodVolumeRestores wait up to the 4h
   PodVolumeOperationTimeout, and the next restore stays queued (no status)
   behind it. Recovered only by force-deleting the stale Restore + PVR CRs.
   DR runbooks need a "sweep stale restores" pre-step. (Inflated AKS→OKE F1c
   trial 1 RTO to 583.8s — excluded from stats, replacement trial run.)

13. **AKS API readiness precedes ARM operation completion.** After
   `az aks start`, the API server serves requests minutes before the ARM
   start operation reaches Succeeded (~8+ min observed); a subsequent
   `az aks stop` is rejected with OperationNotAllowed while the start is
   still "in progress". DR automation that gates on data-plane readiness
   alone will fail when issuing the next control-plane operation — gate on
   provisioningState, not just /readyz.

14. **Restored LoadBalancer Services mint cloud LBs that outlive the
   cluster.** Restoring the app on OKE recreated `frontend-external`
   (type=LoadBalancer) → OKE provisioned a public 100Mbps OCI LB. Deleting
   the node pool and cluster did NOT delete it: it kept billing and held the
   `dr-lb` subnet, failing VCN teardown with 409 "Subnet in use". DR
   runbooks must inventory restore-created cloud resources (LBs, public IPs)
   separately from the cluster lifecycle.
15. **Recovery-site capacity is gated by the full app, not the DR tooling.**
   4x t3.micro ran Velero + bench workloads fine, but the boutique restore
   left 8/12 pods Pending (insufficient memory) — including redis-cart,
   whose PodVolumeRestore then never ran, leaving the Velero restore
   InProgress indefinitely (restore success depends on workload
   schedulability). Sizing the DR site "for the tooling" silently caps
   restore completeness.

(Keep appending; user validates each against logs before it enters the paper.)

7. **StorageClass names do not travel.** Backup captured PVCs referencing the
   source cloud's default SC name (`default` on AKS); restore on OKE left the
   PVC Pending forever (no such SC) while every stateless service restored
   and ran — partial failover that looks healthy at first glance. Fixed with
   Velero's change-storage-class RestoreItemAction ConfigMap, which must be
   pre-provisioned per target cloud (and per direction) BEFORE failover day.
8. **AWS account plan gated the DR site.** Free-Tier account plan refused
   t3.medium nodegroup instances at CloudFormation time (AsgInstanceLaunchFailures,
   "not eligible for Free Tier") ~25 min into provisioning — a capacity/plan
   constraint that only surfaces when standing up the recovery site.
