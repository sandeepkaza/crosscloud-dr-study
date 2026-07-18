# Cloud trial matrix (Paper 3): site A = dr-aks, site B = param. Logs to matrix-cloud.log.
param([string]$CtxB = "dr-oke")
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$kc = "kubectl"
$vl = "velero"
$log = "matrix-cloud.log"
"CLOUD MATRIX START B=$CtxB $(Get-Date -Format o)" | Add-Content $log
foreach ($p in @(
    @{s = "F1c"; n = 3 },
    @{s = "F2c"; n = 3 },
    @{s = "F3c"; n = 3 }
)) {
    "--- scenario $($p.s) x$($p.n) B=$CtxB $(Get-Date -Format o)" | Add-Content $log
    python run_trials_cloud.py --scenario $p.s --trials $p.n --ctx-b $CtxB --kubectl $kc --velero $vl 2>&1 | Add-Content $log
    if ($LASTEXITCODE -ne 0) {
        "SCENARIO $($p.s) FAILED (exit $LASTEXITCODE)" | Add-Content $log
        exit 1
    }
}
"CLOUD MATRIX DONE B=$CtxB $(Get-Date -Format o)" | Add-Content $log
