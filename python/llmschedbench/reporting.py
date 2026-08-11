"""Confidence intervals, static figures, and technical report generation."""

from __future__ import annotations

import json
import math
import os
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from .experiment import (
    MILESTONE_CI_RATE,
    MILESTONE_SEEDS,
    POLICIES,
    build_milestone_specs,
    sha256_file,
)
from .metrics import summarize_run

_COLORS = {
    "least_loaded": "#2563eb",
    "cache_max": "#16a34a",
    "weighted_fair": "#ea580c",
    "slo_guarded_affinity": "#9333ea",
}
_T_CRITICAL_95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776}
_CI_METRICS = {
    "ttft_p95_ms": ("ttft_ms", "p95"),
    "latency_p95_ms": ("latency_ms", "p95"),
    "slo_attainment_rate": ("slo_attainment_rate",),
    "cache_hit_rate": ("cache", "simulator_prefix_hit_rate"),
    "jain_weighted_service": ("fairness", "jain_weighted_service"),
    "starvation_rate": ("starvation_rate",),
    "goodput_rps": ("goodput_rps",),
    "average_npu_utilization": ("load", "average_npu_utilization"),
    "agent_workflow_p95_ms": (
        "per_tenant",
        "coding_agent",
        "workflow_completion_ms",
        "p95",
    ),
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _metric(summary: dict[str, Any], path: tuple[str, ...]) -> float:
    value: Any = summary
    for name in path:
        value = value[name]
    return float(value)


def confidence_interval(values: list[float]) -> dict[str, float | int]:
    """Return a two-sided 95% t interval across deterministic seeds."""
    if not values:
        raise ValueError("confidence interval requires at least one value")
    mean = statistics.fmean(values)
    if len(values) == 1:
        margin = 0.0
    else:
        critical = _T_CRITICAL_95.get(len(values), 1.96)
        margin = critical * statistics.stdev(values) / math.sqrt(len(values))
    return {
        "n": len(values),
        "mean": mean,
        "lower": mean - margin,
        "upper": mean + margin,
        "margin": margin,
    }


def aggregate_confidence_intervals(
    summaries: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, float | int]]]:
    """Aggregate the balanced 1.6-arrivals/s seed slice by policy."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for summary in summaries:
        spec = summary["spec"]
        if float(spec["arrival_rate_rps"]) == MILESTONE_CI_RATE:
            grouped[str(spec["policy"])].append(summary)
    result: dict[str, dict[str, dict[str, float | int]]] = {}
    for policy in POLICIES:
        rows = sorted(grouped.get(policy, []), key=lambda row: row["spec"]["seed"])
        if not rows:
            continue
        result[policy] = {
            name: confidence_interval([_metric(row, path) for row in rows])
            for name, path in _CI_METRICS.items()
        }
    return result


def _line_plot(summaries: list[dict[str, Any]]) -> str:
    width, height = 900, 520
    left, right, top, bottom = 90, 40, 55, 90
    plot_width = width - left - right
    plot_height = height - top - bottom
    rows = [summary for summary in summaries if int(summary["spec"]["seed"]) == 1729]
    values = [float(summary["ttft_ms"]["p95"]) for summary in rows]
    y_max = max(values, default=1.0) * 1.12
    rates = [1.0, 1.6, 2.2]

    def x_position(rate: float) -> float:
        return left + (rate - min(rates)) / (max(rates) - min(rates)) * plot_width

    def y_position(value: float) -> float:
        return top + plot_height - value / y_max * plot_height

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="450" y="30" text-anchor="middle" font-family="sans-serif" font-size="21" font-weight="600">Single-seed p95 TTFT across offered load</text>',
    ]
    for tick in range(6):
        value = y_max * tick / 5
        y = y_position(value)
        elements.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#e5e7eb"/>'
        )
        elements.append(
            f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" font-family="sans-serif" font-size="12">{value:.0f}</text>'
        )
    for rate in rates:
        x = x_position(rate)
        elements.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top+plot_height}" stroke="#f3f4f6"/>'
        )
        elements.append(
            f'<text x="{x:.1f}" y="{top+plot_height+26}" text-anchor="middle" font-family="sans-serif" font-size="13">{rate:g}</text>'
        )
    for policy in POLICIES:
        policy_rows = sorted(
            (row for row in rows if row["spec"]["policy"] == policy),
            key=lambda row: row["spec"]["arrival_rate_rps"],
        )
        if not policy_rows:
            continue
        points = [
            (
                x_position(float(row["spec"]["arrival_rate_rps"])),
                y_position(float(row["ttft_ms"]["p95"])),
            )
            for row in policy_rows
        ]
        rendered = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        color = _COLORS[policy]
        elements.append(
            f'<polyline points="{rendered}" fill="none" stroke="{color}" stroke-width="3"/>'
        )
        elements.extend(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{color}"/>'
            for x, y in points
        )
    legend_y = height - 22
    for index, policy in enumerate(POLICIES):
        x = 105 + index * 195
        elements.append(
            f'<line x1="{x}" y1="{legend_y}" x2="{x+28}" y2="{legend_y}" stroke="{_COLORS[policy]}" stroke-width="4"/>'
        )
        elements.append(
            f'<text x="{x+36}" y="{legend_y+5}" font-family="sans-serif" font-size="12">{escape(policy)}</text>'
        )
    elements.extend(
        [
            f'<text x="{left+plot_width/2:.1f}" y="{height-52}" text-anchor="middle" font-family="sans-serif" font-size="14">Top-level arrivals per second</text>',
            f'<text x="22" y="{top+plot_height/2:.1f}" transform="rotate(-90 22 {top+plot_height/2:.1f})" text-anchor="middle" font-family="sans-serif" font-size="14">p95 TTFT (ms)</text>',
            "</svg>",
        ]
    )
    return "\n".join(elements) + "\n"


def _ci_plot(intervals: dict[str, dict[str, dict[str, float | int]]]) -> str:
    width, height = 900, 520
    left, right, top, bottom = 90, 40, 55, 100
    plot_width = width - left - right
    plot_height = height - top - bottom
    available = [policy for policy in POLICIES if policy in intervals]
    values = [float(intervals[policy]["ttft_p95_ms"]["upper"]) for policy in available]
    y_max = max(values, default=1.0) * 1.12

    def y_position(value: float) -> float:
        return top + plot_height - value / y_max * plot_height

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="450" y="30" text-anchor="middle" font-family="sans-serif" font-size="21" font-weight="600">Five-seed p95 TTFT at 1.6 arrivals/s</text>',
    ]
    for tick in range(6):
        value = y_max * tick / 5
        y = y_position(value)
        elements.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#e5e7eb"/>'
        )
        elements.append(
            f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" font-family="sans-serif" font-size="12">{value:.0f}</text>'
        )
    slot = plot_width / max(len(available), 1)
    for index, policy in enumerate(available):
        interval = intervals[policy]["ttft_p95_ms"]
        mean = float(interval["mean"])
        lower = float(interval["lower"])
        upper = float(interval["upper"])
        x = left + slot * (index + 0.5)
        bar_width = min(105, slot * 0.55)
        y = y_position(mean)
        baseline = top + plot_height
        color = _COLORS[policy]
        elements.append(
            f'<rect x="{x-bar_width/2:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{baseline-y:.1f}" fill="{color}" opacity="0.78"/>'
        )
        elements.append(
            f'<line x1="{x:.1f}" y1="{y_position(upper):.1f}" x2="{x:.1f}" y2="{y_position(lower):.1f}" stroke="#111827" stroke-width="2"/>'
        )
        elements.append(
            f'<line x1="{x-9:.1f}" y1="{y_position(upper):.1f}" x2="{x+9:.1f}" y2="{y_position(upper):.1f}" stroke="#111827" stroke-width="2"/>'
        )
        elements.append(
            f'<line x1="{x-9:.1f}" y1="{y_position(lower):.1f}" x2="{x+9:.1f}" y2="{y_position(lower):.1f}" stroke="#111827" stroke-width="2"/>'
        )
        elements.append(
            f'<text x="{x:.1f}" y="{baseline+24}" text-anchor="middle" font-family="sans-serif" font-size="12">{escape(policy)}</text>'
        )
        elements.append(
            f'<text x="{x:.1f}" y="{baseline+43}" text-anchor="middle" font-family="sans-serif" font-size="11">n={int(interval["n"])}</text>'
        )
    elements.extend(
        [
            f'<text x="22" y="{top+plot_height/2:.1f}" transform="rotate(-90 22 {top+plot_height/2:.1f})" text-anchor="middle" font-family="sans-serif" font-size="14">p95 TTFT (ms, mean ± 95% CI)</text>',
            "</svg>",
        ]
    )
    return "\n".join(elements) + "\n"


def _percent(value: float) -> str:
    return f"{100 * value:.2f}%"


def _render_interval(
    values: dict[str, dict[str, float | int]],
    name: str,
    *,
    percent: bool = False,
) -> str:
    interval = values[name]
    scale = 100 if percent else 1
    suffix = "%" if percent else ""
    return (
        f"{scale * float(interval['mean']):.2f} "
        f"[{scale * float(interval['lower']):.2f}, "
        f"{scale * float(interval['upper']):.2f}]{suffix}"
    )


def _technical_report(
    summaries: list[dict[str, Any]],
    intervals: dict[str, dict[str, dict[str, float | int]]],
    *,
    expected: int,
    generated_at: str,
) -> str:
    primary = sorted(
        (row for row in summaries if int(row["spec"]["seed"]) == MILESTONE_SEEDS[0]),
        key=lambda row: (row["spec"]["arrival_rate_rps"], POLICIES.index(row["spec"]["policy"])),
    )
    lines = [
        "# LLMSchedBench bounded simulation report",
        "",
        f"Generated: {generated_at}",
        "",
        "## Scope and completion",
        "",
        (
            f"This report contains {len(summaries)} of {expected} planned immutable "
            "simulator runs. The primary load matrix uses seed 1729 and is reported "
            "separately from the five-seed 1.6-arrivals/s confidence-interval slice."
        ),
        "",
        "## Single-seed load matrix",
        "",
        "These values are descriptive single-seed results; they are not confidence intervals.",
        "",
        "| Load | Policy | p95 TTFT (ms) | p95 latency (ms) | SLO attainment | Prefix hit | Call goodput/s | Jain fairness | Horizon starvation | NPU util. |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in primary:
        spec = row["spec"]
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{float(spec['arrival_rate_rps']):g}",
                    str(spec["policy"]),
                    f"{row['ttft_ms']['p95']:.2f}",
                    f"{row['latency_ms']['p95']:.2f}",
                    _percent(row["slo_attainment_rate"]),
                    _percent(row["cache"]["simulator_prefix_hit_rate"]),
                    f"{row['goodput_rps']:.3f}",
                    f"{row['fairness']['jain_weighted_service']:.3f}",
                    _percent(row["starvation_rate"]),
                    _percent(row["load"]["average_npu_utilization"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "![Single-seed p95 TTFT](figures/load-sweep.svg)",
            "",
            "## Five-seed confidence intervals at 1.6 arrivals/s",
            "",
            (
                "Intervals are two-sided 95% Student-t intervals across deterministic "
                "workload seeds. A row with n<5 is preliminary and is not a completed "
                "milestone interval."
            ),
            "",
            "| Policy | Seeds | p95 TTFT mean [95% CI] ms | SLO attainment mean [95% CI] | Prefix hit mean [95% CI] | Goodput mean [95% CI] |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for policy in POLICIES:
        if policy not in intervals:
            continue
        values = intervals[policy]

        lines.append(
            f"| {policy} | {values['ttft_p95_ms']['n']} | "
            f"{_render_interval(values, 'ttft_p95_ms')} | "
            f"{_render_interval(values, 'slo_attainment_rate', percent=True)} | "
            f"{_render_interval(values, 'cache_hit_rate', percent=True)} | "
            f"{_render_interval(values, 'goodput_rps')} |"
        )
    lines.extend(
        [
            "",
            "![Five-seed p95 TTFT](figures/ci-slice.svg)",
            "",
            "## Metric definitions",
            "",
            "- TTFT and completion latency percentiles are calculated over simulator call rows and reported per tenant in the machine-readable summaries.",
            "- SLO attainment means TTFT is at or below the scenario-defined tenant SLO; the SLOs are controlled assumptions, not production measurements.",
            "- Call goodput counts SLO-attaining calls completed during the fixed offered-load window, divided by that window's duration. Agent sessions can contribute multiple dependent calls.",
            "- Prefix hit rate is the simulator's avoided prompt-token count divided by total input tokens and is cross-checked against routing-decision cache estimates.",
            "- Weighted service fairness is Jain's index over per-tenant completed token service divided by scenario tenant weight within the fixed offered-load window.",
            "- Horizon starvation is the share of calls released by the end of the fixed offered-load window that have not completed by that horizon. Severe SLO starvation (TTFT over 5× SLO) is retained separately in JSON.",
            "- NPU utilization uses the benchmark cluster's neutral 0/0/1 power meter, where active NPU-seconds divided by simulated duration and worker count equals utilization.",
            "",
            "## Reproducibility and limitations",
            "",
            "Every summary records the run-manifest, workload, decision-log, and simulator-result checksums. Raw runs remain outside Git because they are reproducible and bulky; compact summaries, figures, and this report are intended for version control. Results are simulator measurements under Linux AMD64 emulation on Apple Silicon, not real-GPU validation. The bounded milestone covers the controlled 60/25/15 base mix only; agent-burst, low-prefix-reuse, fairness/affinity ablations, and real vLLM validation remain future work.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_report(
    run_root: str | Path,
    output_root: str | Path,
    *,
    cluster_config: str | Path,
) -> dict[str, Any]:
    """Validate completed runs and generate compact report artifacts."""
    runs = Path(run_root) / "runs"
    output = Path(output_root)
    run_directories = sorted(
        path
        for path in runs.iterdir()
        if path.is_dir() and not path.name.endswith(".incomplete")
    )
    summaries = [
        summarize_run(path, cluster_config=cluster_config) for path in run_directories
    ]
    summaries.sort(
        key=lambda row: (
            row["spec"]["arrival_rate_rps"],
            row["spec"]["seed"],
            POLICIES.index(row["spec"]["policy"]),
        )
    )
    if not summaries:
        raise ValueError(f"no completed runs found under {runs}")
    generated_at = _utc_now()
    intervals = aggregate_confidence_intervals(summaries)
    scenario = str(summaries[0]["spec"]["scenario"])
    expected_keys = {spec.key for spec in build_milestone_specs(scenario)}
    completed_keys = {summary["run_key"] for summary in summaries}

    output.mkdir(parents=True, exist_ok=True)
    jsonl = "".join(json.dumps(row, sort_keys=True) + "\n" for row in summaries)
    _atomic_text(output / "run-summaries.jsonl", jsonl)
    aggregate = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "planned_runs": len(expected_keys),
        "completed_runs": len(completed_keys),
        "missing_runs": sorted(expected_keys - completed_keys),
        "confidence_intervals": intervals,
        "source_run_manifest_sha256": {
            row["run_key"]: row["source_artifacts"]["run_manifest_sha256"]
            for row in summaries
        },
    }
    _atomic_json(output / "aggregate.json", aggregate)
    _atomic_text(output / "figures" / "load-sweep.svg", _line_plot(summaries))
    _atomic_text(output / "figures" / "ci-slice.svg", _ci_plot(intervals))
    _atomic_text(
        output / "report.md",
        _technical_report(
            summaries,
            intervals,
            expected=len(expected_keys),
            generated_at=generated_at,
        ),
    )
    artifacts = [
        output / "run-summaries.jsonl",
        output / "aggregate.json",
        output / "figures" / "load-sweep.svg",
        output / "figures" / "ci-slice.svg",
        output / "report.md",
    ]
    manifest = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "completed_runs": len(completed_keys),
        "planned_runs": len(expected_keys),
        "artifacts": {
            str(path.relative_to(output)): {
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
            for path in artifacts
        },
    }
    _atomic_json(output / "manifest.json", manifest)
    return manifest
