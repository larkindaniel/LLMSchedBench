"""Traceable metrics for completed LLMSchedBench simulator runs."""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from .experiment import sha256_file, validate_completed_run
from .saturation import parse_simulator_utilization

_PREFIX_HIT_TOKENS = re.compile(
    r"^NPU prefix hit prompt tokens:\s+([0-9]+)\s*$", re.MULTILINE
)
_PREFIX_HIT_RATIO = re.compile(
    r"^NPU prefix hit ratio \(%\):\s+([0-9.]+)\s*$", re.MULTILINE
)


def percentile(values: list[float], probability: float) -> float:
    """Return a linearly interpolated percentile for finite values."""
    if not 0 <= probability <= 1:
        raise ValueError("probability must be between zero and one")
    ordered = sorted(value for value in values if math.isfinite(value))
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: list[float]) -> dict[str, float]:
    finite = [value for value in values if math.isfinite(value)]
    return {
        "mean": statistics.fmean(finite) if finite else 0.0,
        "p50": percentile(finite, 0.50),
        "p95": percentile(finite, 0.95),
        "p99": percentile(finite, 0.99),
    }


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _read_results(path: Path) -> list[dict[str, int | float | str]]:
    integer_fields = {
        "instance id",
        "request id",
        "input",
        "output",
        "arrival",
        "end_time",
        "latency",
        "queuing_delay",
        "TTFT",
    }
    float_fields = {"TPOT"}
    rows: list[dict[str, int | float | str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row: dict[str, int | float | str] = dict(raw)
            for name in integer_fields:
                row[name] = int(raw[name])
            for name in float_fields:
                row[name] = float(raw[name])
            rows.append(row)
    if not rows:
        raise ValueError(f"simulator result has no request rows: {path}")
    request_ids = [int(row["request id"]) for row in rows]
    if len(request_ids) != len(set(request_ids)):
        raise ValueError(f"simulator result has duplicate request IDs: {path}")
    return rows


def _jain(values: list[float]) -> float:
    if not values or not any(values):
        return 0.0
    return sum(values) ** 2 / (len(values) * sum(value**2 for value in values))


def _variance(values: list[float]) -> float:
    return statistics.pvariance(values) if len(values) > 1 else 0.0


def summarize_run(
    run_directory: str | Path,
    *,
    cluster_config: str | Path,
) -> dict[str, Any]:
    """Validate one immutable run and calculate all benchmark metrics."""
    root = Path(run_directory)
    manifest = validate_completed_run(root)
    scenario = _read_json(root / "resolved-scenario.json")
    provenance = _read_json(root / "workload-provenance.json")
    request_map = _read_json(root / "request-map.json")
    results = _read_results(root / "simulator-results.csv")
    decisions = _read_jsonl(root / "decisions.jsonl")

    map_by_internal = {
        int(value["internal_request_id"]): value for value in request_map
    }
    result_by_internal = {int(row["request id"]): row for row in results}
    if set(map_by_internal) != set(result_by_internal):
        raise ValueError("request-map IDs do not exactly match simulator result IDs")

    admitted: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        if decision.get("admit", True):
            request_id = str(decision["request_id"])
            if request_id in admitted:
                raise ValueError(f"request admitted more than once: {request_id}")
            admitted[request_id] = decision
    external_to_internal = {
        str(value["request_id"]): internal
        for internal, value in map_by_internal.items()
    }
    if set(admitted) != set(external_to_internal):
        raise ValueError("admitted decisions do not exactly match workload requests")
    for request_id, decision in admitted.items():
        internal = external_to_internal[request_id]
        actual_worker = str(result_by_internal[internal]["instance id"])
        if str(decision["worker_id"]) != actual_worker:
            raise ValueError(f"routing decision/result worker mismatch: {request_id}")

    tenant_configs = scenario["tenants"]
    starvation_multiplier = float(
        scenario.get("metrics", {}).get("starvation_slo_multiplier", 5.0)
    )
    enriched: list[dict[str, Any]] = []
    for internal, metadata in map_by_internal.items():
        result = result_by_internal[internal]
        tenant = str(metadata["tenant"])
        slo_ns = round(float(tenant_configs[tenant]["ttft_slo_ms"]) * 1_000_000)
        enriched.append(
            {
                **metadata,
                "instance_id": int(result["instance id"]),
                "arrival_ns": int(result["arrival"]),
                "end_ns": int(result["end_time"]),
                "latency_ns": int(result["latency"]),
                "queuing_delay_ns": int(result["queuing_delay"]),
                "ttft_ns": int(result["TTFT"]),
                "tpot_ns": float(result["TPOT"]),
                "slo_ns": slo_ns,
                "slo_attained": int(result["TTFT"]) <= slo_ns,
                "severely_starved": int(result["TTFT"])
                > starvation_multiplier * slo_ns,
            }
        )

    tenants = sorted(tenant_configs)
    per_tenant: dict[str, Any] = {}
    for tenant in tenants:
        rows = [row for row in enriched if row["tenant"] == tenant]
        workflows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            workflows[str(row["session_id"])].append(row)
        workflow_ms = [
            (max(item["end_ns"] for item in group) - min(item["arrival_ns"] for item in group))
            / 1_000_000
            for group in workflows.values()
        ]
        per_tenant[tenant] = {
            "requests": len(rows),
            "input_tokens": sum(int(row["input_tokens"]) for row in rows),
            "output_tokens": sum(int(row["output_tokens"]) for row in rows),
            "ttft_ms": _distribution([row["ttft_ns"] / 1_000_000 for row in rows]),
            "latency_ms": _distribution(
                [row["latency_ns"] / 1_000_000 for row in rows]
            ),
            "workflow_completion_ms": _distribution(workflow_ms),
            "slo_attainment_rate": (
                sum(row["slo_attained"] for row in rows) / len(rows) if rows else 0.0
            ),
            "severe_starvation_rate": (
                sum(row["severely_starved"] for row in rows) / len(rows)
                if rows
                else 0.0
            ),
        }

    first_arrival_ns = min(row["arrival_ns"] for row in enriched)
    measurement_seconds = float(provenance["entries"]) / float(
        manifest["spec"]["arrival_rate_rps"]
    )
    horizon_ns = first_arrival_ns + round(measurement_seconds * 1_000_000_000)
    offered = [row for row in enriched if row["arrival_ns"] <= horizon_ns]
    completed_in_window = [row for row in offered if row["end_ns"] <= horizon_ns]
    good = [row for row in completed_in_window if row["slo_attained"]]
    starved = [row for row in offered if row["end_ns"] > horizon_ns]

    service_tokens: dict[str, int] = {}
    for tenant in tenants:
        service_tokens[tenant] = sum(
            int(row["input_tokens"]) + int(row["output_tokens"])
            for row in completed_in_window
            if row["tenant"] == tenant
        )
    normalized_service = [
        service_tokens[tenant] / float(tenant_configs[tenant]["weight"])
        for tenant in tenants
    ]
    total_service = sum(service_tokens.values())
    weighted_service_share = {
        tenant: service_tokens[tenant] / total_service if total_service else 0.0
        for tenant in tenants
    }

    admitted_decisions = list(admitted.values())
    decision_cache_hits = sum(
        int(decision.get("cache_hit_tokens", 0)) for decision in admitted_decisions
    )
    input_tokens = sum(int(row["input_tokens"]) for row in enriched)
    log_text = (root / "simulator.log").read_text(encoding="utf-8")
    logged_hit_tokens = int(_PREFIX_HIT_TOKENS.search(log_text).group(1))
    logged_hit_ratio = float(_PREFIX_HIT_RATIO.search(log_text).group(1)) / 100
    utilization = parse_simulator_utilization(root / "simulator.log", cluster_config)

    worker_requests: dict[int, int] = defaultdict(int)
    worker_tokens: dict[int, int] = defaultdict(int)
    for row in enriched:
        worker = int(row["instance_id"])
        worker_requests[worker] += 1
        worker_tokens[worker] += int(row["input_tokens"]) + int(row["output_tokens"])
    worker_ids = list(range(int(utilization["workers"])))

    ttft_ms = [row["ttft_ns"] / 1_000_000 for row in enriched]
    latency_ms = [row["latency_ns"] / 1_000_000 for row in enriched]
    deferred = sum(not decision.get("admit", True) for decision in decisions)
    summary = {
        "schema_version": "1.0.0",
        "run_key": root.name,
        "run_directory": str(root),
        "spec": manifest["spec"],
        "requests": len(enriched),
        "input_tokens": input_tokens,
        "output_tokens": sum(int(row["output_tokens"]) for row in enriched),
        "ttft_ms": _distribution(ttft_ms),
        "latency_ms": _distribution(latency_ms),
        "slo_attainment_rate": sum(row["slo_attained"] for row in enriched)
        / len(enriched),
        "severe_starvation_rate": sum(
            row["severely_starved"] for row in enriched
        )
        / len(enriched),
        "measurement_window_seconds": measurement_seconds,
        "measurement_horizon_ns": horizon_ns,
        "offered_by_horizon": len(offered),
        "completed_by_horizon": len(completed_in_window),
        "starved_at_horizon": len(starved),
        "starvation_rate": len(starved) / len(offered) if offered else 0.0,
        "goodput_rps": len(good) / measurement_seconds,
        "cache": {
            "decision_prefill_tokens_avoided": decision_cache_hits,
            "decision_cache_hit_rate": (
                decision_cache_hits / input_tokens if input_tokens else 0.0
            ),
            "simulator_prefix_hit_tokens": logged_hit_tokens,
            "simulator_prefix_hit_rate": logged_hit_ratio,
        },
        "fairness": {
            "jain_weighted_service": _jain(normalized_service),
            "weighted_service_share": weighted_service_share,
            "service_tokens_by_tenant": service_tokens,
        },
        "load": {
            "average_npu_utilization": utilization["average_npu_utilization"],
            "active_npu_seconds": utilization["active_npu_seconds"],
            "simulated_duration_seconds": utilization["duration_seconds"],
            "worker_request_variance": _variance(
                [float(worker_requests[worker]) for worker in worker_ids]
            ),
            "worker_token_variance": _variance(
                [float(worker_tokens[worker]) for worker in worker_ids]
            ),
        },
        "routing": {
            "decision_records": len(decisions),
            "admitted_requests": len(admitted_decisions),
            "defer_records": deferred,
            "defer_rate": deferred / len(decisions) if decisions else 0.0,
        },
        "per_tenant": per_tenant,
        "source_artifacts": {
            "run_manifest_sha256": sha256_file(root / "manifest.json"),
            "simulator_results_sha256": manifest["outputs"]["simulator-results.csv"][
                "sha256"
            ],
            "decisions_sha256": manifest["outputs"]["decisions.jsonl"]["sha256"],
            "workload_sha256": manifest["outputs"]["workload.jsonl"]["sha256"],
        },
    }
    return summary
