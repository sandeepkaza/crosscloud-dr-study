# Paper 3 real-cloud testbed provisioning (detached; logs to provision.log).
# AKS = site A (primary), EKS = site B (recovery). OKE added separately.
# Cost discipline: 2 small nodes/cluster, same-day teardown (teardown.ps1).
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$log = "provision.log"
"PROVISION START $(Get-Date -Format o)" | Out-File $log

$eksctl = "eksctl"

# --- AKS (async create, then wait) ---
"az group create..." | Add-Content $log
az group create -n dr-paper3 -l eastus -o none 2>&1 | Add-Content $log
"az aks create (background)..." | Add-Content $log
az aks create -g dr-paper3 -n dr-aks -l eastus `
    --node-count 2 -s Standard_B2s --tier free `
    --generate-ssh-keys --no-wait -o none 2>&1 | Add-Content $log

# --- EKS (blocking; ~15-20 min CloudFormation) ---
"eksctl create cluster..." | Add-Content $log
& $eksctl create cluster --name dr-eks --region us-east-1 `
    --nodes 2 --node-type t3.medium --managed 2>&1 | Add-Content $log
if ($LASTEXITCODE -ne 0) { "EKS CREATE FAILED" | Add-Content $log }

# --- wait for AKS, fetch kubeconfig contexts ---
"az aks wait..." | Add-Content $log
az aks wait -g dr-paper3 -n dr-aks --created --timeout 1800 2>&1 | Add-Content $log
az aks get-credentials -g dr-paper3 -n dr-aks --context dr-aks --overwrite-existing 2>&1 | Add-Content $log
aws eks update-kubeconfig --name dr-eks --region us-east-1 --alias dr-eks 2>&1 | Add-Content $log

# --- S3 bucket = cross-cloud replication channel ---
aws s3api create-bucket --bucket dr-paper3-velero-<AWS_ACCOUNT_ID> --region us-east-1 2>&1 | Add-Content $log

"PROVISION DONE $(Get-Date -Format o)" | Add-Content $log
