"""fig_rig.png — detailed deployment diagram of the three-cloud DR rig
(diagrams + graphviz; dot must be on PATH). 300 dpi for print."""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.storage import SimpleStorageServiceS3
from diagrams.azure.compute import AKS
from diagrams.aws.compute import EKS
from diagrams.oci.compute import OKE
from diagrams.k8s.compute import Deploy
from diagrams.k8s.storage import PV
from diagrams.k8s.podconfig import CM
from diagrams.onprem.client import Client

graph_attr = {
    "dpi": "300",
    "fontsize": "13",
    "pad": "0.2",
    "splines": "spline",
    "nodesep": "0.35",
    "ranksep": "0.55",
}
node_attr = {"fontsize": "11"}

with Diagram(
    "",
    filename="fig_rig",
    outformat="png",
    show=False,
    graph_attr=graph_attr,
    node_attr=node_attr,
    direction="LR",
):
    orch = Client("orchestrator\n(detect / runbook /\ninject, P3-P5)")

    with Cluster("Site A - Azure AKS (eastus), primary"):
        aks = AKS("dr-aks\nK8s 1.35, 2 nodes")
        with Cluster("workloads"):
            boutique = Deploy("Online Boutique\n11 services")
            redis = PV("redis-cart PV")
            writer = PV("sequenced writer\nPV (RPO instrument)")
        velero_a = Deploy("Velero 1.18.2\n+ node-agent (FSB/kopia)\nschedule: 1 min")

    bucket = SimpleStorageServiceS3("S3 bucket (us-east-1)\nBSL for all sites\nkopia repository")

    with Cluster("Site B1 - Amazon EKS (us-east-1), recovery"):
        eks = EKS("dr-eks\nK8s 1.34, 7 nodes")
        velero_b1 = Deploy("Velero + node-agent")
        cm_b1 = CM("SC remap +\nrestore-helper CMs")

    with Cluster("Site B2 - Oracle OKE (ap-hyderabad-1), recovery"):
        oke = OKE("dr-oke\nK8s 1.36, 2 nodes")
        velero_b2 = Deploy("Velero + node-agent")
        cm_b2 = CM("SC remap +\nrestore-helper CMs")

    boutique >> velero_a
    redis >> velero_a
    writer >> velero_a
    velero_a >> Edge(label="FSB backup\n(objects + PV contents)") >> bucket
    bucket >> Edge(label="restore") >> velero_b1
    bucket >> Edge(label="restore") >> velero_b2
    velero_b1 >> cm_b1
    velero_b2 >> cm_b2

    orch >> Edge(style="dashed", label="probe / inject\nF1c F2c F3c") >> aks
    orch >> Edge(style="dashed", label="locate + restore + gate") >> eks
    orch >> Edge(style="dashed") >> oke
