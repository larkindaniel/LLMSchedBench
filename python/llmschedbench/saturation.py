"""Helpers for controlled arrival-rate sweeps and utilization measurement."""

from __future__ import annotations

import json
import os
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from .calibration import sha256_file

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CLOCKS = re.compile(r"Total clocks \(ns\):\s+([0-9]+)")
_NPU_ENERGY = re.compile(r"NPU energy consumption \(J\):\s+([0-9.]+)")


def _cluster_meter(path: str | Path) -> tuple[int, dict[str, Any]]:
    cluster = json.loads(Path(path).read_text(encoding="utf-8"))
    total_npus = 0
    meter: dict[str, Any] | None = None
    for node in cluster.get("nodes", ()):
        for instance in node.get("instances", ()):
            total_npus += int(instance.get("num_npus", 1))
            hardware = str(instance["hardware"])
            candidate = node.get("power", {}).get("npu", {}).get(hardware)
            if meter is None:
                meter = candidate
            elif candidate != meter:
                raise ValueError("all workers must use the same utilization meter")
    if total_npus <= 0 or meter is None:
        raise ValueError("cluster must contain workers and NPU power metering")
    expected = {"idle_power": 0, "standby_power": 0, "active_power": 1}
    if any(float(meter.get(key, -1)) != value for key, value in expected.items()):
        raise ValueError("utilization meter must use idle=0, standby=0, active=1")
    return total_npus, meter


def parse_simulator_utilization(
    log_path: str | Path, cluster_config_path: str | Path
) -> dict[str, Any]:
    """Recover average simulated NPU active time from the 0/0/1 meter."""
    log = Path(log_path)
    text = _ANSI_ESCAPE.sub("", log.read_text(encoding="utf-8"))
    clocks = _CLOCKS.findall(text)
    energy = _NPU_ENERGY.findall(text)
    if len(clocks) != 1 or len(energy) != 1:
        raise ValueError("simulator log must contain one clocks and one NPU energy result")
    total_npus, _ = _cluster_meter(cluster_config_path)
    duration_seconds = int(clocks[0]) / 1_000_000_000
    active_npu_seconds = float(energy[0])
    utilization = active_npu_seconds / (duration_seconds * total_npus)
    if not 0 <= utilization <= 1.0001:
        raise ValueError(f"calculated utilization is outside [0, 1]: {utilization}")
    return {
        "active_npu_seconds": active_npu_seconds,
        "average_npu_utilization": min(utilization, 1.0),
        "cluster_config_sha256": sha256_file(cluster_config_path),
        "duration_seconds": duration_seconds,
        "log_sha256": sha256_file(log),
        "workers": total_npus,
    }


def rescale_workload_arrivals(
    source_path: str | Path,
    destination_path: str | Path,
    *,
    arrival_rate_rps: float,
) -> int:
    """Reschedule top-level workload entries while preserving closed-loop steps."""
    if arrival_rate_rps <= 0:
        raise ValueError("arrival_rate_rps must be positive")
    source = Path(source_path)
    records = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records.sort(key=lambda row: (int(row["arrival_time_ns"]), str(row)))
    rate = Fraction(str(arrival_rate_rps))
    for slot, record in enumerate(records):
        record["arrival_time_ns"] = round(Fraction(slot * 1_000_000_000, 1) / rate)

    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        os.replace(temporary, destination)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
    return len(records)
