"""Deterministic construction of controlled three-tenant workload mixes."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..scenario import compile_scenario
from ..schema import TENANTS, NormalizedCall, write_parquet
from .simulator import simulator_records, write_simulator_jsonl
from .sources import sha256_file


def allocate_entries(total: int, traffic: dict[str, float]) -> dict[str, int]:
    """Allocate an exact entry count with deterministic largest remainders."""
    if total <= 0:
        raise ValueError("total entries must be positive")
    if set(traffic) != TENANTS:
        raise ValueError("traffic must define exactly the three benchmark tenants")

    exact = {tenant: total * float(traffic[tenant]) for tenant in TENANTS}
    allocated = {tenant: int(exact[tenant]) for tenant in TENANTS}
    remainder = total - sum(allocated.values())
    order = sorted(
        TENANTS,
        key=lambda tenant: (-(exact[tenant] - allocated[tenant]), tenant),
    )
    for tenant in order[:remainder]:
        allocated[tenant] += 1
    return allocated


def _tenant_seed(seed: int, tenant: str) -> int:
    digest = hashlib.sha256(f"{seed}:{tenant}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _window(values: list[Any], count: int, *, seed: int, tenant: str) -> list[Any]:
    if count == 0:
        return []
    if len(values) < count:
        raise ValueError(
            f"tenant {tenant} needs {count} entries but only {len(values)} are eligible"
        )
    window_count = len(values) - count + 1
    start = _tenant_seed(seed, tenant) % window_count
    return values[start : start + count]


def _calls_from_table(table: Any) -> list[NormalizedCall]:
    return [NormalizedCall.from_mapping(row) for row in table.to_pylist()]


def _select_qwen(
    path: Path,
    allocations: dict[str, int],
    *,
    seed: int,
) -> list[NormalizedCall]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    metadata = pq.read_table(
        path,
        columns=["tenant", "session_arrival_ns", "request_id"],
    )
    tenant_values = metadata["tenant"].to_pylist()
    arrival_values = metadata["session_arrival_ns"].to_pylist()
    request_values = metadata["request_id"].to_pylist()
    indices_by_tenant: dict[str, list[int]] = defaultdict(list)
    for index, tenant in enumerate(tenant_values):
        if tenant in {"chat", "api_batch"}:
            indices_by_tenant[tenant].append(index)

    selected_indices: list[int] = []
    for tenant in ("chat", "api_batch"):
        ordered = sorted(
            indices_by_tenant[tenant],
            key=lambda index: (arrival_values[index], request_values[index]),
        )
        selected_indices.extend(
            _window(ordered, allocations[tenant], seed=seed, tenant=tenant)
        )

    table = pq.read_table(path)
    return _calls_from_table(table.take(pa.array(selected_indices, type=pa.int64())))


def _select_tracelab(
    path: Path,
    count: int,
    *,
    seed: int,
    constraints: dict[str, Any],
) -> list[NormalizedCall]:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    metadata = pq.read_table(
        path,
        columns=["session_id", "step_index", "session_arrival_ns", "input_tokens"],
    )
    sessions: dict[str, dict[str, Any]] = {}
    columns = [metadata[name].to_pylist() for name in metadata.column_names]
    for session_id, step_index, arrival, input_tokens in zip(*columns):
        state = sessions.setdefault(
            session_id,
            {
                "arrival": arrival,
                "steps": [],
                "max_input_tokens": 0,
            },
        )
        state["arrival"] = min(state["arrival"], arrival)
        state["steps"].append(step_index)
        state["max_input_tokens"] = max(state["max_input_tokens"], input_tokens)

    min_steps = int(constraints.get("min_steps", 1))
    max_steps = int(constraints.get("max_steps", 0))
    max_input_tokens = int(constraints.get("max_input_tokens", 0))
    eligible: list[tuple[int, str]] = []
    for session_id, state in sessions.items():
        steps = sorted(state["steps"])
        if steps != list(range(len(steps))):
            continue
        if len(steps) < min_steps:
            continue
        if max_steps and len(steps) > max_steps:
            continue
        if max_input_tokens and state["max_input_tokens"] > max_input_tokens:
            continue
        eligible.append((state["arrival"], session_id))
    eligible.sort()
    selected = _window(eligible, count, seed=seed, tenant="coding_agent")
    selected_ids = [session_id for _, session_id in selected]

    table = pq.read_table(path)
    filtered = table.filter(
        pc.is_in(table["session_id"], value_set=pa.array(selected_ids))
    )
    calls = _calls_from_table(filtered)
    selected_order = {session_id: index for index, session_id in enumerate(selected_ids)}
    calls.sort(
        key=lambda call: (
            selected_order[call.session_id],
            call.step_index,
            call.request_id,
        )
    )
    return calls


def _schedule_arrivals(
    calls: list[NormalizedCall],
    allocations: dict[str, int],
    *,
    seed: int,
    arrival_rate_rps: float,
) -> list[NormalizedCall]:
    """Resample selected entries onto a controlled aggregate arrival stream."""
    groups: dict[str, deque[list[NormalizedCall]]] = {}
    for tenant in ("chat", "api_batch"):
        tenant_calls = sorted(
            (call for call in calls if call.tenant == tenant),
            key=lambda call: (call.session_arrival_ns, call.request_id),
        )
        groups[tenant] = deque([[call] for call in tenant_calls])

    coding_by_session: dict[str, list[NormalizedCall]] = defaultdict(list)
    for call in calls:
        if call.tenant == "coding_agent":
            coding_by_session[call.session_id].append(call)
    coding_groups = sorted(
        coding_by_session.values(),
        key=lambda group: (
            min(call.session_arrival_ns for call in group),
            group[0].session_id,
        ),
    )
    groups["coding_agent"] = deque(coding_groups)

    labels = [
        (tenant, ordinal)
        for tenant in sorted(TENANTS)
        for ordinal in range(allocations[tenant])
    ]
    labels.sort(
        key=lambda item: hashlib.sha256(
            f"{seed}:mix-slot:{item[0]}:{item[1]}".encode()
        ).digest()
    )
    rate = Fraction(str(arrival_rate_rps))
    scheduled: list[NormalizedCall] = []
    for slot, (tenant, _) in enumerate(labels):
        arrival_ns = round(Fraction(slot * 1_000_000_000, 1) / rate)
        group = groups[tenant].popleft()
        scheduled.extend(
            replace(call, session_arrival_ns=arrival_ns) for call in group
        )
    if any(groups[tenant] for tenant in TENANTS):
        raise AssertionError("selected entry counts do not match traffic allocation")

    return sorted(
        scheduled,
        key=lambda call: (
            call.session_arrival_ns,
            call.tenant,
            call.session_id,
            call.step_index,
            call.request_id,
        ),
    )


def build_mixed_trace(
    scenario_path: str | Path,
    qwen_path: str | Path,
    tracelab_path: str | Path,
    *,
    output_path: str | Path,
    simulator_output_path: str | Path,
    entries: int | None = None,
) -> dict[str, Any]:
    """Build canonical Parquet and LLMServingSim JSONL for one scenario mix."""
    scenario_source = Path(scenario_path)
    qwen_source = Path(qwen_path)
    tracelab_source = Path(tracelab_path)
    scenario = compile_scenario(scenario_source)
    trace_config = dict(scenario.get("trace", {}))
    total_entries = int(entries if entries is not None else trace_config.get("entries", 0))
    if total_entries <= 0:
        raise ValueError("scenario trace.entries or --entries must be positive")

    traffic = {tenant: float(scenario["traffic"][tenant]) for tenant in TENANTS}
    allocations = allocate_entries(total_entries, traffic)
    seed = int(scenario["seed"])
    qwen_calls = _select_qwen(qwen_source, allocations, seed=seed)
    coding_constraints = dict(trace_config.get("coding_session", {}))
    coding_calls = _select_tracelab(
        tracelab_source,
        allocations["coding_agent"],
        seed=seed,
        constraints=coding_constraints,
    )
    arrival_rate_rps = float(trace_config.get("arrival_rate_rps", 0))
    if arrival_rate_rps <= 0:
        raise ValueError("scenario trace.arrival_rate_rps must be positive")
    calls = _schedule_arrivals(
        qwen_calls + coding_calls,
        allocations,
        seed=seed,
        arrival_rate_rps=arrival_rate_rps,
    )

    output = Path(output_path)
    simulator_output = Path(simulator_output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(calls, str(output))
    write_simulator_jsonl(calls, simulator_output)
    records = simulator_records(calls)
    realized_entries = {
        "chat": 0,
        "coding_agent": sum("sub_requests" in record for record in records),
        "api_batch": 0,
    }
    # Flat simulator records omit tenant labels, so canonical calls are the source
    # of truth for their entry counts. Closed-loop calls are counted by session.
    realized_entries["chat"] = sum(call.tenant == "chat" for call in calls)
    realized_entries["api_batch"] = sum(call.tenant == "api_batch" for call in calls)

    return {
        "allocations": allocations,
        "arrival_rate_rps": arrival_rate_rps,
        "calls": len(calls),
        "entries": len(records),
        "inputs": {
            "qwen": {
                "name": qwen_source.name,
                "sha256": sha256_file(qwen_source),
                "size": qwen_source.stat().st_size,
            },
            "tracelab": {
                "name": tracelab_source.name,
                "sha256": sha256_file(tracelab_source),
                "size": tracelab_source.stat().st_size,
            },
            "scenario": {
                "name": scenario_source.name,
                "sha256": sha256_file(scenario_source),
                "size": scenario_source.stat().st_size,
            },
        },
        "outputs": {
            "parquet": {
                "name": output.name,
                "sha256": sha256_file(output),
                "size": output.stat().st_size,
            },
            "simulator_jsonl": {
                "name": simulator_output.name,
                "sha256": sha256_file(simulator_output),
                "size": simulator_output.stat().st_size,
            },
        },
        "realized_entries": realized_entries,
        "scenario": scenario["name"],
        "seed": seed,
        "selection": {"coding_session": coding_constraints},
        "traffic": traffic,
    }


def write_mix_provenance(value: dict[str, Any], path: str | Path) -> None:
    """Write deterministic mix provenance atomically."""
    import os

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
