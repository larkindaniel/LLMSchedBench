"""Normalization adapters for Qwen-Bailian and TraceLab JSONL traces."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from ..prefixes import (
    tracelab_append_descriptor,
    tracelab_bootstrap_descriptor,
    tracelab_reuse_descriptor,
)
from ..schema import SCHEMA_VERSION, NormalizedCall


class NormalizationError(ValueError):
    """A source row is malformed or violates source accounting invariants."""


def _open_text(path: str | Path) -> TextIO:
    source = Path(path)
    if source.suffix == ".gz":
        return gzip.open(source, "rt", encoding="utf-8")
    return source.open(encoding="utf-8")


def _jsonl(path: str | Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with _open_text(path) as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise NormalizationError(
                    f"{path}:{line_number}: invalid JSON: {error.msg}"
                ) from error
            if not isinstance(row, dict):
                raise NormalizationError(f"{path}:{line_number}: row must be an object")
            yield line_number, row


def _required(row: dict[str, Any], key: str, path: str | Path, line: int) -> Any:
    if key not in row or row[key] is None:
        raise NormalizationError(f"{path}:{line}: missing required field {key!r}")
    return row[key]


def _nonnegative_int(value: Any, field: str, path: str | Path, line: int) -> int:
    if isinstance(value, bool):
        raise NormalizationError(f"{path}:{line}: {field} must be an integer")
    try:
        converted = int(value)
    except (TypeError, ValueError) as error:
        raise NormalizationError(f"{path}:{line}: {field} must be an integer") from error
    if converted < 0:
        raise NormalizationError(f"{path}:{line}: {field} must be non-negative")
    return converted


def _request_id(source: str, *parts: object) -> str:
    identity = ":".join(str(part) for part in parts)
    digest = hashlib.sha256(f"{source}:{identity}".encode()).hexdigest()[:20]
    return f"{source}:{digest}"


def iter_normalize_qwen(
    path: str | Path,
    *,
    tenant: str,
    trace_id: str | None = None,
) -> Iterator[NormalizedCall]:
    """Normalize one Qwen To-C or To-B JSONL trace."""
    if tenant not in {"chat", "api_batch"}:
        raise ValueError("Qwen tenant must be 'chat' or 'api_batch'")
    trace_name = trace_id or Path(path).name.removesuffix(".jsonl")
    session_by_request: dict[int, str] = {}
    for line_number, row in _jsonl(path):
        chat_id = _nonnegative_int(
            _required(row, "chat_id", path, line_number), "chat_id", path, line_number
        )
        parent = int(_required(row, "parent_chat_id", path, line_number))
        if chat_id in session_by_request:
            raise NormalizationError(f"{path}:{line_number}: duplicate chat_id {chat_id}")
        if parent == -1:
            session_id = f"qwen:{trace_name}:session:{chat_id}"
        else:
            try:
                session_id = session_by_request[parent]
            except KeyError as error:
                raise NormalizationError(
                    f"{path}:{line_number}: parent_chat_id {parent} has not appeared"
                ) from error
        session_by_request[chat_id] = session_id

        timestamp = float(_required(row, "timestamp", path, line_number))
        if not math.isfinite(timestamp) or timestamp < 0:
            raise NormalizationError(
                f"{path}:{line_number}: timestamp must be finite and non-negative"
            )
        input_tokens = _nonnegative_int(
            _required(row, "input_length", path, line_number),
            "input_length",
            path,
            line_number,
        )
        output_tokens = _nonnegative_int(
            _required(row, "output_length", path, line_number),
            "output_length",
            path,
            line_number,
        )
        hashes = _required(row, "hash_ids", path, line_number)
        if not isinstance(hashes, list):
            raise NormalizationError(f"{path}:{line_number}: hash_ids must be a list")
        expected_blocks = math.ceil(input_tokens / 16) if input_tokens else 0
        if len(hashes) != expected_blocks:
            raise NormalizationError(
                f"{path}:{line_number}: expected {expected_blocks} hash blocks for "
                f"{input_tokens} tokens, got {len(hashes)}"
            )

        turn = _nonnegative_int(
            _required(row, "turn", path, line_number), "turn", path, line_number
        )
        if turn == 0:
            raise NormalizationError(f"{path}:{line_number}: turn must start at 1")
        source_record_id = f"{trace_name}:{chat_id}"
        yield NormalizedCall(
            schema_version=SCHEMA_VERSION,
            source="qwen_bailian",
            source_record_id=source_record_id,
            request_id=_request_id("qwen", trace_name, chat_id),
            tenant=tenant,
            workload_class=str(row.get("type") or "unknown"),
            session_id=session_id,
            step_index=turn - 1,
            dependency_mode="open_loop",
            session_arrival_ns=round(timestamp * 1_000_000_000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prefix_block_ids=tuple(
                f"qwen:{trace_name}:block:{block_hash}" for block_hash in hashes
            ),
            tool_delay_after_ns=0,
        )


def normalize_qwen(
    path: str | Path,
    *,
    tenant: str,
    trace_id: str | None = None,
) -> list[NormalizedCall]:
    """Materialize one Qwen trace; intended for fixtures and small windows."""
    return list(iter_normalize_qwen(path, tenant=tenant, trace_id=trace_id))


def _parse_timestamp(value: Any, path: str | Path, line: int) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise NormalizationError(
            f"{path}:{line}: invalid ISO timestamp {value!r}"
        ) from error


def _event_times(
    row: dict[str, Any], path: str | Path, line: int
) -> list[tuple[str, datetime]]:
    events = row.get("timing_events", [])
    if not isinstance(events, list):
        raise NormalizationError(f"{path}:{line}: timing_events must be a list")
    values: list[tuple[str, datetime]] = []
    for event in events:
        if not isinstance(event, dict):
            raise NormalizationError(f"{path}:{line}: timing event must be an object")
        timestamp = _parse_timestamp(event.get("timestamp"), path, line)
        if timestamp is not None:
            values.append((str(event.get("event_type") or "unknown"), timestamp))
    return values


def _tool_delay_ns(
    row: dict[str, Any], event_times: list[tuple[str, datetime]], path: str | Path, line: int
) -> int:
    output_types = {"reasoning", "text", "tool_call"}
    output_times = [timestamp for kind, timestamp in event_times if kind in output_types]
    tools = row.get("tools", [])
    if not isinstance(tools, list):
        raise NormalizationError(f"{path}:{line}: tools must be a list")
    result_times: list[datetime] = []
    fallback_ms = 0
    for tool in tools:
        if not isinstance(tool, dict):
            raise NormalizationError(f"{path}:{line}: tool must be an object")
        result = _parse_timestamp(tool.get("result_at"), path, line)
        if result is not None:
            result_times.append(result)
        latency = tool.get("tool_internal_latency_ms")
        if latency is None:
            latency = tool.get("tool_wall_latency_ms")
        if latency is not None:
            fallback_ms = max(fallback_ms, _nonnegative_int(latency, "tool latency", path, line))
    if output_times and result_times:
        seconds = (max(result_times) - max(output_times)).total_seconds()
        return max(0, round(seconds * 1_000_000_000))
    return fallback_ms * 1_000_000


def iter_normalize_tracelab(path: str | Path) -> Iterator[NormalizedCall]:
    """Normalize a TraceLab release JSONL or JSONL.GZ trace."""
    previous_request_by_session: dict[str, str] = {}
    for line_number, row in _jsonl(path):
        provider = str(_required(row, "provider", path, line_number))
        session_id = str(_required(row, "session_id", path, line_number))
        round_id = str(_required(row, "round_id", path, line_number))
        step_index = _nonnegative_int(
            _required(row, "round_index", path, line_number),
            "round_index",
            path,
            line_number,
        )
        input_tokens = _nonnegative_int(
            _required(row, "input_tokens_total", path, line_number),
            "input_tokens_total",
            path,
            line_number,
        )
        prefix_tokens = _nonnegative_int(
            _required(row, "prefix_tokens", path, line_number),
            "prefix_tokens",
            path,
            line_number,
        )
        append_tokens = _nonnegative_int(
            _required(row, "newly_append_tokens", path, line_number),
            "newly_append_tokens",
            path,
            line_number,
        )
        output_tokens = _nonnegative_int(
            _required(row, "output_tokens", path, line_number),
            "output_tokens",
            path,
            line_number,
        )
        if prefix_tokens + append_tokens != input_tokens:
            raise NormalizationError(
                f"{path}:{line_number}: prefix_tokens + newly_append_tokens must equal "
                "input_tokens_total"
            )

        event_times = _event_times(row, path, line_number)
        if not event_times:
            raise NormalizationError(f"{path}:{line_number}: no usable timing timestamp")
        arrival = min(timestamp for _, timestamp in event_times)
        arrival_ns = round(arrival.timestamp() * 1_000_000_000)

        source_record_id = f"line:{line_number}:{provider}:{round_id}"
        request_id = _request_id("tracelab", line_number, provider, session_id, round_id)
        total_blocks = math.ceil(input_tokens / 16) if input_tokens else 0
        cached_blocks = min(total_blocks, prefix_tokens // 16)
        descriptors: list[str] = []
        if cached_blocks:
            previous_request = previous_request_by_session.get(session_id)
            if previous_request is None:
                descriptors.append(
                    tracelab_bootstrap_descriptor(session_id, cached_blocks)
                )
            else:
                descriptors.append(
                    tracelab_reuse_descriptor(previous_request, cached_blocks)
                )
        appended_blocks = total_blocks - cached_blocks
        if appended_blocks:
            descriptors.append(tracelab_append_descriptor(request_id, appended_blocks))
        previous_request_by_session[session_id] = request_id

        yield NormalizedCall(
            schema_version=SCHEMA_VERSION,
            source="tracelab",
            source_record_id=source_record_id,
            request_id=request_id,
            tenant="coding_agent",
            workload_class=provider,
            session_id=f"tracelab:{session_id}",
            step_index=step_index,
            dependency_mode="closed_loop",
            session_arrival_ns=arrival_ns,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prefix_block_ids=tuple(descriptors),
            tool_delay_after_ns=_tool_delay_ns(row, event_times, path, line_number),
        )


def normalize_tracelab(path: str | Path) -> list[NormalizedCall]:
    """Materialize one TraceLab trace; intended for fixtures and small windows."""
    return list(iter_normalize_tracelab(path))


def iter_normalize_paths(
    source: str, paths: Iterable[str | Path]
) -> Iterator[NormalizedCall]:
    """Stream explicit source paths using filename-based Qwen tenant mapping."""
    for path in paths:
        if source == "tracelab":
            yield from iter_normalize_tracelab(path)
        elif source == "qwen":
            name = Path(path).name.lower()
            if "tracea" in name:
                tenant = "chat"
            elif "traceb" in name:
                tenant = "api_batch"
            else:
                raise ValueError(
                    f"cannot infer Qwen tenant from {path}; filename must contain traceA or traceB"
                )
            yield from iter_normalize_qwen(path, tenant=tenant)
        else:
            raise ValueError(f"unknown data source: {source}")


def normalize_paths(source: str, paths: Iterable[str | Path]) -> list[NormalizedCall]:
    """Materialize explicit source paths; intended for fixtures and small windows."""
    return list(iter_normalize_paths(source, paths))
