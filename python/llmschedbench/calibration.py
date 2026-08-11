"""Generate unloaded simulator workloads and estimate worker service rates."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

CALIBRATION_SCHEMA_VERSION = "1.0.0"
DEFAULT_PREFILL_INPUT_TOKENS = (128, 512, 2048, 4096)
DEFAULT_DECODE_OUTPUT_TOKENS = (32, 64, 128, 256)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_cluster_workers(path: str | Path) -> int:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    workers = sum(
        len(node.get("instances", ())) for node in value.get("nodes", ())
    )
    if workers <= 0:
        raise ValueError("cluster configuration must contain at least one instance")
    return workers


def build_unloaded_workload(
    *,
    workers: int,
    gap_seconds: float = 30.0,
    prefill_input_tokens: Sequence[int] = DEFAULT_PREFILL_INPUT_TOKENS,
    decode_output_tokens: Sequence[int] = DEFAULT_DECODE_OUTPUT_TOKENS,
    decode_input_tokens: int = 128,
) -> list[dict[str, int]]:
    """Build an RR-balanced workload whose requests cannot overlap in time."""
    if workers <= 0:
        raise ValueError("workers must be positive")
    if not math.isfinite(gap_seconds) or gap_seconds <= 0:
        raise ValueError("gap_seconds must be positive and finite")
    if decode_input_tokens <= 0:
        raise ValueError("decode_input_tokens must be positive")
    if len(set(prefill_input_tokens)) < 2:
        raise ValueError("at least two distinct prefill input sizes are required")
    if len(set(decode_output_tokens)) < 2:
        raise ValueError("at least two distinct decode output sizes are required")
    if any(value <= 0 for value in prefill_input_tokens):
        raise ValueError("prefill input sizes must be positive")
    if any(value < 2 for value in decode_output_tokens):
        raise ValueError("decode output sizes must be at least two")

    gap_ns = round(gap_seconds * 1_000_000_000)
    requests: list[dict[str, int]] = []
    arrival_ns = 1
    samples = [
        (int(input_tokens), 2) for input_tokens in prefill_input_tokens
    ] + [
        (int(decode_input_tokens), int(output_tokens))
        for output_tokens in decode_output_tokens
    ]
    for input_tokens, output_tokens in samples:
        # Repeating each size once per worker makes round-robin routing deliver
        # every calibration point to every instance.
        for _ in range(workers):
            requests.append(
                {
                    "input_toks": input_tokens,
                    "output_toks": output_tokens,
                    "arrival_time_ns": arrival_ns,
                }
            )
            arrival_ns += gap_ns
    return requests


def write_unloaded_workload(
    requests: Iterable[dict[str, int]], path: str | Path
) -> int:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    count = 0
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for request in requests:
                handle.write(json.dumps(request, sort_keys=True) + "\n")
                count += 1
        os.replace(temporary, destination)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
    return count


def _linear_fit(points: Sequence[tuple[float, float]]) -> dict[str, float]:
    if len(points) < 2 or len({x for x, _ in points}) < 2:
        raise ValueError("calibration needs at least two distinct sample sizes")
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    intercept = mean_y - slope * mean_x
    if not math.isfinite(slope) or slope <= 0:
        raise ValueError("calibration produced a non-positive service-time slope")
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in points)
    total = sum((y - mean_y) ** 2 for _, y in points)
    r_squared = 1.0 if total == 0 and residual == 0 else 1.0 - residual / total
    return {
        "intercept_ns": intercept,
        "r_squared": r_squared,
        "slope_ns_per_token": slope,
        "tokens_per_second": 1_000_000_000 / slope,
    }


def estimate_service_rates(
    csv_path: str | Path,
    *,
    prefill_output_tokens: int = 2,
    decode_input_tokens: int = 128,
    max_overlap_ns: int = 0,
    expected_workers: int | None = None,
    workload_path: str | Path | None = None,
    cluster_config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Fit linear prefill and decode rates from isolated request results."""
    source = Path(csv_path)
    by_worker: dict[str, dict[str, list[tuple[float, float]]]] = defaultdict(
        lambda: {"prefill": [], "decode": []}
    )
    intervals: list[tuple[int, int, str]] = []
    row_count = 0
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "arrival",
            "end_time",
            "instance id",
            "input",
            "output",
            "latency",
            "TTFT",
        }
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                "simulator output missing columns: " + ", ".join(sorted(missing))
            )
        for row in reader:
            row_count += 1
            worker_id = str(row["instance id"])
            input_tokens = int(row["input"])
            output_tokens = int(row["output"])
            latency_ns = int(row["latency"])
            arrival_ns = int(row["arrival"])
            end_time_ns = int(row["end_time"])
            ttft_ns = int(row["TTFT"])
            intervals.append((arrival_ns, end_time_ns, worker_id))
            if output_tokens == prefill_output_tokens:
                by_worker[worker_id]["prefill"].append(
                    (float(input_tokens), float(ttft_ns))
                )
            if (
                input_tokens == decode_input_tokens
                and output_tokens > prefill_output_tokens
            ):
                remaining_tokens = output_tokens - 1
                decode_time_ns = latency_ns - ttft_ns
                if decode_time_ns <= 0:
                    raise ValueError("decode calibration time must be positive")
                by_worker[worker_id]["decode"].append(
                    (float(remaining_tokens), float(decode_time_ns))
                )

    if not by_worker:
        raise ValueError("simulator output contains no calibration rows")
    if expected_workers is not None and len(by_worker) != expected_workers:
        raise ValueError(
            f"expected results for {expected_workers} workers, found {len(by_worker)}"
        )

    latest_completion = -1
    for arrival_ns, end_time_ns, worker_id in sorted(intervals):
        if arrival_ns + max_overlap_ns < latest_completion:
            overlap_ns = latest_completion - arrival_ns
            raise ValueError(
                f"calibration is not unloaded: worker {worker_id} arrived "
                f"{overlap_ns} ns before an earlier request completed"
            )
        latest_completion = max(latest_completion, end_time_ns)

    worker_rates: dict[str, dict[str, float]] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(by_worker, key=lambda value: int(value)):
        samples = by_worker[worker_id]
        prefill = _linear_fit(samples["prefill"])
        decode = _linear_fit(samples["decode"])
        worker_rates[worker_id] = {
            "prefill_tokens_per_second": prefill["tokens_per_second"],
            "decode_tokens_per_second": decode["tokens_per_second"],
        }
        diagnostics[worker_id] = {
            "decode": {**decode, "samples": len(samples["decode"])},
            "prefill": {**prefill, "samples": len(samples["prefill"])},
        }

    provenance: dict[str, Any] = {
        "simulator_results": {
            "path": str(source.resolve()),
            "rows": row_count,
            "sha256": sha256_file(source),
        }
    }
    for name, path in (
        ("workload", workload_path),
        ("cluster_config", cluster_config_path),
    ):
        if path is not None:
            artifact = Path(path)
            provenance[name] = {
                "path": str(artifact.resolve()),
                "sha256": sha256_file(artifact),
            }

    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "method": {
            "decode": "OLS of post-first-token latency against output_tokens - 1",
            "prefill": "OLS of TTFT against input tokens",
            "unloaded_validation": "each arrival follows all prior completions",
            "unloaded_overlap_tolerance_ns": max_overlap_ns,
        },
        "source": provenance,
        "workers": worker_rates,
        "diagnostics": diagnostics,
    }


def write_calibration(value: dict[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, destination)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
