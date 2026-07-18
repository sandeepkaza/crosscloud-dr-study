# EKS (site B) provisioning — us-east-1. Logs to provision-eks.log.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$log = "provision-eks.log"
# $env:AWS_PROFILE = "<your-profile>"
"EKS PROVISION START $(Get-Date -Format o) " | Out-File $log
$eksctl = "eksctl"
& $eksctl create cluster --name dr-eks --region us-east-1 `
    --nodes 4 --node-type t3.micro --managed 2>&1 | Add-Content $log
if ($LASTEXITCODE -ne 0) {
    "EKS PROVISION FAILED" | Add-Content $log
    exit 1
}
aws eks update-kubeconfig --name dr-eks --region us-east-1 --alias dr-eks 2>&1 | Add-Content $log
"EKS PROVISION DONE $(Get-Date -Format o)" | Add-Content $log
