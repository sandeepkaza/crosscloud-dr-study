# OKE (site C) provisioning — ap-hyderabad-1, tenancy root compartment.
# Lab-grade ephemeral network (public subnets, permissive SL) — torn down
# same day by teardown.ps1. Logs to provision-oke.log.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING = "True"
$log = "provision-oke.log"
$C = "<TENANCY_OCID>"
$R = "ap-hyderabad-1"
function Log($m) { $m | Add-Content $log }
"OKE PROVISION START $(Get-Date -Format o)" | Out-File $log

function OciJson([string[]]$cliArgs) {
    $out = & oci @cliArgs --region $R 2>&1
    $text = $out -join "`n"
    Log $text
    # oci --wait-for-state prepends "Action completed..." before the JSON
    $i = $text.IndexOf("{")
    if ($i -lt 0) { return $null }
    try { return $text.Substring($i) | ConvertFrom-Json } catch { return $null }
}

# 1. VCN (reuse if a previous run already created it)
$existing = OciJson @("network","vcn","list","--compartment-id",$C,"--display-name","dr-vcn")
$vcnId = $existing.data[0].id
if (-not $vcnId) {
    $vcn = OciJson @("network","vcn","create","--compartment-id",$C,"--cidr-block","10.0.0.0/16","--display-name","dr-vcn","--dns-label","drvcn","--wait-for-state","AVAILABLE")
    $vcnId = $vcn.data.id
}
if (-not $vcnId) { Log "OKE FAILED: vcn"; exit 1 }
Log "vcn: $vcnId"

# 2. Internet gateway + default route (reuse if present)
$igList = OciJson @("network","internet-gateway","list","--compartment-id",$C,"--vcn-id",$vcnId)
$igId = $igList.data[0].id
if (-not $igId) {
    $ig = OciJson @("network","internet-gateway","create","--compartment-id",$C,"--vcn-id",$vcnId,"--is-enabled","true","--display-name","dr-ig","--wait-for-state","AVAILABLE")
    $igId = $ig.data.id
}
$rt = OciJson @("network","vcn","get","--vcn-id",$vcnId)
$rtId = $rt.data.'default-route-table-id'
$null = OciJson @("network","route-table","update","--rt-id",$rtId,"--route-rules",('[{"destination":"0.0.0.0/0","networkEntityId":"' + $igId + '"}]'),"--force")

# 3. Open default security list (ephemeral lab)
$slId = $rt.data.'default-security-list-id'
$ingress = '[{"protocol":"all","source":"10.0.0.0/16"},{"protocol":"6","source":"0.0.0.0/0","tcpOptions":{"destinationPortRange":{"min":6443,"max":6443}}},{"protocol":"6","source":"0.0.0.0/0","tcpOptions":{"destinationPortRange":{"min":80,"max":80}}},{"protocol":"6","source":"0.0.0.0/0","tcpOptions":{"destinationPortRange":{"min":22,"max":22}}}]'
$egress = '[{"protocol":"all","destination":"0.0.0.0/0"}]'
$null = OciJson @("network","security-list","update","--security-list-id",$slId,"--ingress-security-rules",$ingress,"--egress-security-rules",$egress,"--force")

# 4. Subnets (regional, public; reuse if present)
function EnsureSubnet($cidr, $name, $dns) {
    $found = OciJson @("network","subnet","list","--compartment-id",$C,"--vcn-id",$vcnId,"--display-name",$name)
    if ($found.data[0].id) { return $found.data[0].id }
    $sn = OciJson @("network","subnet","create","--compartment-id",$C,"--vcn-id",$vcnId,"--cidr-block",$cidr,"--display-name",$name,"--dns-label",$dns,"--wait-for-state","AVAILABLE")
    return $sn.data.id
}
$epId   = EnsureSubnet "10.0.0.0/28" "dr-endpoint" "ep"
$nodeId = EnsureSubnet "10.0.1.0/24" "dr-nodes" "nodes"
$lbId   = EnsureSubnet "10.0.2.0/24" "dr-lb" "lb"
if (-not ($epId -and $nodeId -and $lbId)) { Log "OKE FAILED: subnets"; exit 1 }

# 5. Cluster (basic tier, public endpoint)
$cl = OciJson @("ce","cluster","create","--compartment-id",$C,"--name","dr-oke","--kubernetes-version","v1.36.1","--vcn-id",$vcnId,"--endpoint-subnet-id",$epId,"--service-lb-subnet-ids",('["' + $lbId + '"]'),"--endpoint-public-ip-enabled","true","--wait-for-state","SUCCEEDED","--max-wait-seconds","1800")
Start-Sleep -Seconds 5
$clusters = OciJson @("ce","cluster","list","--compartment-id",$C,"--name","dr-oke","--lifecycle-state","ACTIVE")
$clusterId = $clusters.data[0].id
if (-not $clusterId) { Log "OKE FAILED: cluster"; exit 1 }
Log "cluster: $clusterId"

# 6. Node pool: latest OKE image for the version, 2x E4.Flex 1ocpu/8gb
$opts = OciJson @("ce","node-pool-options","get","--node-pool-option-id","all")
$img = ($opts.data.sources | Where-Object { $_.'source-name' -match 'Oracle-Linux.*OKE-1\.36\.1' -and $_.'source-name' -notmatch 'aarch64|GPU' } | Select-Object -First 1).'image-id'
if (-not $img) { $img = ($opts.data.sources | Where-Object { $_.'source-name' -notmatch 'aarch64|GPU' } | Select-Object -First 1).'image-id' }
Log "node image: $img"
$ad = (OciJson @("iam","availability-domain","list","--compartment-id",$C)).data[0].name
Log "AD: $ad"
$placement = '[{"availabilityDomain":"' + $ad + '","subnetId":"' + $nodeId + '"}]'
$np = OciJson @("ce","node-pool","create","--cluster-id",$clusterId,"--compartment-id",$C,"--name","dr-nodes","--kubernetes-version","v1.36.1","--node-shape","VM.Standard.E4.Flex","--node-shape-config",'{"ocpus":1,"memoryInGBs":8}',"--node-image-id",$img,"--size","2","--placement-configs",$placement,"--wait-for-state","SUCCEEDED","--max-wait-seconds","1800")

# 7. kubeconfig context
$null = OciJson @("ce","cluster","create-kubeconfig","--cluster-id",$clusterId,"--file","$env:USERPROFILE\.kube\config","--token-version","2.0.0","--kube-endpoint","PUBLIC_ENDPOINT")
Log "OKE PROVISION DONE $(Get-Date -Format o)"
