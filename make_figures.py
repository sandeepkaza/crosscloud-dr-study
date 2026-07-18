"""Figures for Paper 3 (IEEEtran, single-column ~3.5in), from trial CSVs.

fig_rto_decomp.pdf — median RTO decomposition (detect/locate/restore/ready)
as stacked bars across the three rigs x two failover scenarios.
fig_rpo.pdf — per-trial RPO vs the 1-min backup cadence band, by rig.

Okabe-Ito colors, validated CVD-safe (adjacent-pair floor covered by fixed
segment order, white spacers, and legend). Excluded trials per results.md:
OKE F1c trial 1 (ops-finding 12).
"""

import csv
import statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PHASES = [  # fixed stack order, baseline-up
    ("detect_s", "detect", "#0072B2"),
    ("locate_s", "locate", "#E69F00"),
    ("restore_s", "restore", "#009E73"),
    ("ready_s", "ready", "#CC79A7"),
]

RIGS = [  # (bar label, csv, scenario name in file, exclude predicate)
    ("local\nF1", "trials.csv", "F1", None),
    ("local\nF3", "trials.csv", "F3", None),
    ("OKE\nF1c", "trials_cloud_dr-oke.csv", "F1c", lambda r: float(r["rto_s"]) > 300),
    ("OKE\nF3c", "trials_cloud_dr-oke.csv", "F3c", None),
    ("EKS\nF1c", "trials_cloud_dr-eks.csv", "F1c", None),
    ("EKS\nF3c", "trials_cloud_dr-eks.csv", "F3c", None),
]


def rows(path, scen, exclude):
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["scenario"] != scen or not r["rto_s"]:
                continue
            if exclude and exclude(r):
                continue
            out.append(r)
    return out


def fig_decomp():
    fig, ax = plt.subplots(figsize=(3.5, 2.4), dpi=300)
    xs = range(len(RIGS))
    for xi, (label, path, scen, excl) in enumerate(RIGS):
        rs = rows(path, scen, excl)
        bottom = 0.0
        for key, _, color in PHASES:
            med = statistics.median(float(r[key]) for r in rs)
            ax.bar(xi, med, 0.62, bottom=bottom, color=color,
                   edgecolor="white", linewidth=0.8)
            bottom += med
        ax.annotate(f"{statistics.median(float(r['rto_s']) for r in rs):.0f}s",
                    xy=(xi, bottom), xytext=(0, 2), textcoords="offset points",
                    ha="center", fontsize=6.5)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([r[0] for r in RIGS], fontsize=6.5)
    ax.set_ylabel("Median time (s), stacked phases", fontsize=7.5)
    ax.tick_params(labelsize=7)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="0.9", lw=0.6)
    ax.set_axisbelow(True)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in PHASES]
    ax.legend(handles, [n for _, n, _ in PHASES], fontsize=6, ncol=4,
              frameon=False, loc="upper left", handlelength=1.0,
              columnspacing=0.9, handletextpad=0.4)
    ax.set_ylim(0, 115)
    fig.tight_layout(pad=0.3)
    fig.savefig("fig_rto_decomp.pdf")
    print("fig_rto_decomp.pdf written")


def fig_rpo():
    groups = [  # label -> per-trial rpo_s across failover scenarios
        ("local", "trials.csv", ("F1", "F3"), None),
        ("OKE", "trials_cloud_dr-oke.csv", ("F1c", "F3c"),
         lambda r: float(r["rto_s"]) > 300),
        ("EKS", "trials_cloud_dr-eks.csv", ("F1c", "F3c"), None),
    ]
    fig, ax = plt.subplots(figsize=(3.5, 2.0), dpi=300)
    ax.axhspan(0, 60, color="0.93", zorder=0)
    ax.annotate("one backup interval (60 s)", xy=(0.02, 60), xytext=(2, -8),
                textcoords="offset points", fontsize=6, color="0.35")
    for xi, (label, path, scens, excl) in enumerate(groups):
        vals = []
        for scen in scens:
            vals += [float(r["rpo_s"]) for r in rows(path, scen, excl)]
        jitter = [xi + (i - (len(vals) - 1) / 2) * 0.055 for i in range(len(vals))]
        ax.scatter(jitter, vals, s=14, color="#0072B2", zorder=3,
                   edgecolor="white", linewidth=0.6)
        med = statistics.median(vals)
        ax.hlines(med, xi - 0.22, xi + 0.22, color="#D55E00", lw=1.4, zorder=4)
        ax.annotate(f"med {med:.0f}s", xy=(xi + 0.24, med), fontsize=6.5,
                    color="#D55E00", va="center")
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([g[0] for g in groups], fontsize=7)
    ax.set_ylabel("RPO (s), per trial", fontsize=7.5)
    ax.set_ylim(0, 95)
    ax.set_xlim(-0.5, 2.75)
    ax.tick_params(labelsize=7)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    fig.tight_layout(pad=0.3)
    fig.savefig("fig_rpo.pdf")
    print("fig_rpo.pdf written")


if __name__ == "__main__":
    fig_decomp()
    fig_rpo()
