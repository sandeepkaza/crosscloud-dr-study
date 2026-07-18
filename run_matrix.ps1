# Full local trial matrix, detached (outlives tool timeouts).
# Writes progress to matrix.log; final line "MATRIX DONE" or "MATRIX FAILED".
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$kc = "kubectl"
$vl = "velero"

Remove-Item trials.csv -ErrorAction SilentlyContinue
"MATRIX START $(Get-Date -Format o)" | Out-File matrix.log

$plan = @(
    @{s = "F3"; n = 5 },
    @{s = "F1"; n = 5 },
    @{s = "F2"; n = 3 },
    @{s = "F4"; n = 2 }
)
foreach ($p in $plan) {
    "--- scenario $($p.s) x$($p.n) $(Get-Date -Format o)" | Add-Content matrix.log
    python run_trials.py --scenario $p.s --trials $p.n --kubectl $kc --velero $vl 2>&1 | Add-Content matrix.log
    if ($LASTEXITCODE -ne 0) {
        "MATRIX FAILED at $($p.s)" | Add-Content matrix.log
        exit 1
    }
}
"MATRIX DONE $(Get-Date -Format o)" | Add-Content matrix.log
