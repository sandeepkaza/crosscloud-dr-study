# OKE teardown — deletes node pool, cluster, then dr-vcn network. Logs to teardown-oke.log.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING = "True"
$log = "teardown-oke.log"
$C = "<TENANCY_OCID>"
$R = "ap-hyderabad-1"
function Log($m) { $m | Add-Content $log }
"OKE TEARDOWN START $(Get-Date -Format o)" | Out-File $log

function OciJson([string[]]$cliArgs) {
    $out = & oci @cliArgs --region $R 2>&1
    $text = $out -join "`n"
    Log $text
    $i = $text.IndexOf("{")
    if ($i -lt 0) { return $null }
    try { return $text.Substring($i) | ConvertFrom-Json } catch { return $null }
}

$clusters = OciJson @("ce","cluster","list","--compartment-id",$C,"--name","dr-oke","--lifecycle-state","ACTIVE")
$clusterId = $clusters.data[0].id
if ($clusterId) {
    $nps = OciJson @("ce","node-pool","list","--compartment-id",$C,"--cluster-id",$clusterId)
    foreach ($np in $nps.data) {
        Log "deleting node pool $($np.id)"
        $null = OciJson @("ce","node-pool","delete","--node-pool-id",$np.id,"--force","--wait-for-state","SUCCEEDED","--max-wait-seconds","1800")
    }
    Log "deleting cluster $clusterId"
    $null = OciJson @("ce","cluster","delete","--cluster-id",$clusterId,"--force","--wait-for-state","SUCCEEDED","--max-wait-seconds","1800")
}

$vcns = OciJson @("network","vcn","list","--compartment-id",$C,"--display-name","dr-vcn")
$vcnId = $vcns.data[0].id
if ($vcnId) {
    foreach ($sn in (OciJson @("network","subnet","list","--compartment-id",$C,"--vcn-id",$vcnId)).data) {
        Log "deleting subnet $($sn.'display-name')"
        $null = OciJson @("network","subnet","delete","--subnet-id",$sn.id,"--force","--wait-for-state","TERMINATED","--max-wait-seconds","600")
    }
    # route rules must be emptied before IG delete
    $vcn = OciJson @("network","vcn","get","--vcn-id",$vcnId)
    $null = OciJson @("network","route-table","update","--rt-id",$vcn.data.'default-route-table-id',"--route-rules","[]","--force")
    foreach ($ig in (OciJson @("network","internet-gateway","list","--compartment-id",$C,"--vcn-id",$vcnId)).data) {
        Log "deleting internet gateway"
        $null = OciJson @("network","internet-gateway","delete","--ig-id",$ig.id,"--force","--wait-for-state","TERMINATED","--max-wait-seconds","600")
    }
    Log "deleting vcn"
    $null = OciJson @("network","vcn","delete","--vcn-id",$vcnId,"--force","--wait-for-state","TERMINATED","--max-wait-seconds","600")
}
"OKE TEARDOWN DONE $(Get-Date -Format o)" | Add-Content $log
