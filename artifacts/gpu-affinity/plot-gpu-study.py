"""Plot individual study repetitions and paired differences; no significance claim."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("results", type=Path)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
rows = json.loads(a.results.read_text())
conditions = ["low_reuse", "distributed", "hot_prefix"]
labels = ["Little reuse", "Distributed reuse", "Popular prefix"]
policies = ["least_loaded", "cache_max"]
colors = ["#2369a8", "#c05621"]
fig, grid = plt.subplots(2, 2, figsize=(11, 8))
axes = grid.flatten()
for ax, metric, title, scale in zip(
    axes,
    ["p95_ttft_ms", "actual_cache_hit_fraction", "peak_waiting", "p95_latency_ms"],
    [
        "First visible output · p95 (ms)",
        "Measured prefix-cache hits (%)",
        "Peak waiting requests on either worker (sampled)",
        "Completion latency · p95 (ms)",
    ],
    [1, 100, 1, 1],
):
    for pi, policy in enumerate(policies):
        for ci, condition in enumerate(conditions):
            vals = [
                (max(r[metric]) if metric == "peak_waiting" else r[metric]) * scale
                for r in rows
                if r["policy"] == policy
                and r["condition"] == condition
                and r[metric] is not None
            ]
            x = ci + (pi - 0.5) * 0.3
            if vals:
                ax.scatter(
                    x + np.linspace(-0.035, 0.035, len(vals)),
                    vals,
                    color=colors[pi],
                    alpha=0.8,
                    s=35,
                    label=policy if ci == 0 else None,
                )
                ax.plot(
                    [x - 0.085, x + 0.085],
                    [np.mean(vals)] * 2,
                    color=colors[pi],
                    linewidth=2,
                )
    ax.set_xticks(range(3), labels, rotation=15)
    ax.set_title(title, fontsize=11)
    ax.grid(axis="y", alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylim(bottom=0)
axes[0].legend(frameon=False)
fig.suptitle("Does prefix affinity improve real GPU serving?", fontsize=16, y=0.99)
fig.text(
    0.5,
    0.015,
    "Two A6000 workers · Qwen3-4B · matched arrivals and lengths · dots = individual seeds; bars = means\nThree repetitions per condition; descriptive results, not a significance test.",
    ha="center",
    fontsize=9,
)
fig.tight_layout(rect=[0, 0.08, 1, 0.96], h_pad=3)
a.output.mkdir(parents=True, exist_ok=True)
for suffix in ["png", "svg", "pdf"]:
    fig.savefig(a.output / ("prefix-affinity." + suffix), dpi=180, bbox_inches="tight")
