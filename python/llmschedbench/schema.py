"""Canonical, versioned representation of one LLM call."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
TENANTS = frozenset({"chat", "coding_agent", "api_batch"})
DEPENDENCY_MODES = frozenset({"open_loop", "closed_loop"})


@dataclass(frozen=True)
class NormalizedCall:
    schema_version: str
    source: str
    source_record_id: str
    request_id: str
    tenant: str
    workload_class: str
    session_id: str
    step_index: int
    dependency_mode: str
    session_arrival_ns: int
    input_tokens: int
    output_tokens: int
    prefix_block_ids: tuple[str, ...]
    tool_delay_after_ns: int

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version: {self.schema_version}")
        if self.tenant not in TENANTS:
            raise ValueError(f"invalid tenant: {self.tenant}")
        if self.dependency_mode not in DEPENDENCY_MODES:
            raise ValueError(f"invalid dependency mode: {self.dependency_mode}")
        if not self.request_id or not self.session_id or not self.source_record_id:
            raise ValueError("request, session, and source record IDs must be non-empty")
        for name in (
            "step_index",
            "session_arrival_ns",
            "input_tokens",
            "output_tokens",
            "tool_delay_after_ns",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> NormalizedCall:
        data = dict(value)
        data["prefix_block_ids"] = tuple(data.get("prefix_block_ids", ()))
        return cls(**data)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "source_record_id": self.source_record_id,
            "request_id": self.request_id,
            "tenant": self.tenant,
            "workload_class": self.workload_class,
            "session_id": self.session_id,
            "step_index": self.step_index,
            "dependency_mode": self.dependency_mode,
            "session_arrival_ns": self.session_arrival_ns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "prefix_block_ids": list(self.prefix_block_ids),
            "tool_delay_after_ns": self.tool_delay_after_ns,
        }


def arrow_schema():
    """Return the canonical PyArrow schema, importing PyArrow lazily."""
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("schema_version", pa.string(), nullable=False),
            pa.field("source", pa.string(), nullable=False),
            pa.field("source_record_id", pa.string(), nullable=False),
            pa.field("request_id", pa.string(), nullable=False),
            pa.field("tenant", pa.string(), nullable=False),
            pa.field("workload_class", pa.string(), nullable=False),
            pa.field("session_id", pa.string(), nullable=False),
            pa.field("step_index", pa.int32(), nullable=False),
            pa.field("dependency_mode", pa.string(), nullable=False),
            pa.field("session_arrival_ns", pa.int64(), nullable=False),
            pa.field("input_tokens", pa.int64(), nullable=False),
            pa.field("output_tokens", pa.int64(), nullable=False),
            pa.field(
                "prefix_block_ids",
                pa.list_(pa.string()),
                nullable=False,
            ),
            pa.field("tool_delay_after_ns", pa.int64(), nullable=False),
        ],
        metadata={b"llmschedbench.schema_version": SCHEMA_VERSION.encode()},
    )


def write_parquet(rows: Sequence[NormalizedCall], path: str) -> None:
    """Validate and write normalized calls using the canonical schema."""
    import os

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist([row.to_mapping() for row in rows], arrow_schema())
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        pq.write_table(table, temporary, compression="zstd")
        os.replace(temporary, destination)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def write_parquet_stream(
    rows: Iterable[NormalizedCall],
    path: str,
    *,
    batch_size: int = 256,
) -> int:
    """Write canonical calls in bounded batches and return the row count."""
    import os

    import pyarrow as pa
    import pyarrow.parquet as pq

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    if temporary.exists():
        temporary.unlink()

    schema = arrow_schema()
    writer = None
    count = 0
    batch: list[NormalizedCall] = []
    try:
        writer = pq.ParquetWriter(temporary, schema, compression="zstd")
        for row in rows:
            batch.append(row)
            if len(batch) < batch_size:
                continue
            table = pa.Table.from_pylist(
                [item.to_mapping() for item in batch], schema=schema
            )
            writer.write_table(table)
            count += len(batch)
            batch.clear()
        if batch:
            table = pa.Table.from_pylist(
                [item.to_mapping() for item in batch], schema=schema
            )
            writer.write_table(table)
            count += len(batch)
        writer.close()
        writer = None
        os.replace(temporary, destination)
    except BaseException:
        if writer is not None:
            writer.close()
        if temporary.exists():
            temporary.unlink()
        raise
    return count


def read_parquet(path: str) -> list[NormalizedCall]:
    """Read and validate canonical normalized calls."""
    import pyarrow.parquet as pq

    table = pq.read_table(path, schema=arrow_schema())
    return [NormalizedCall.from_mapping(row) for row in table.to_pylist()]
