"""Reproduce descriptive aggregate values and paired differences from run summaries."""

import argparse
import json
import statistics
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("results", type=Path)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
rows = json.loads(a.results.read_text())
expected = {
    (c, s, p)
    for c in ["low_reuse", "distributed", "hot_prefix"]
    for s in [1729, 2718, 3141]
    for p in ["least_loaded", "cache_max"]
}
assert (
    len(rows) == 18
    and {(r["condition"], r["seed"], r["policy"]) for r in rows} == expected
), "Incomplete or duplicate matrix"
aggregate = []
for condition in ["low_reuse", "distributed", "hot_prefix"]:
    groups = {
        p: [r for r in rows if r["condition"] == condition and r["policy"] == p]
        for p in ["least_loaded", "cache_max"]
    }
    item = {"condition": condition}
    for policy, rs in groups.items():
        item[policy] = {
            "mean_run_p95_ttft_ms": statistics.mean(r["p95_ttft_ms"] for r in rs),
            "mean_run_p95_completion_ms": statistics.mean(
                r["p95_latency_ms"] for r in rs
            ),
            "mean_cache_hit_pct": 100
            * statistics.mean(r["actual_cache_hit_fraction"] for r in rs),
            "max_waiting": max(max(r["peak_waiting"]) for r in rs),
        }
    item["paired_ttft_reduction_pct"] = []
    for seed in [1729, 2718, 3141]:
        ll = next(r["p95_ttft_ms"] for r in groups["least_loaded"] if r["seed"] == seed)
        cm = next(r["p95_ttft_ms"] for r in groups["cache_max"] if r["seed"] == seed)
        item["paired_ttft_reduction_pct"].append(100 * (ll - cm) / ll)
    aggregate.append(item)
a.output.write_text(json.dumps(aggregate, indent=2) + "\n")
