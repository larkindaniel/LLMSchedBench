"""Conversion from canonical calls to LLMServingSim workload JSONL."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from ..prefixes import (
    TRACELAB_APPEND_PREFIX,
    TRACELAB_BOOTSTRAP_PREFIX,
    TRACELAB_REUSE_PREFIX,
    parse_tracelab_descriptor,
    synthetic_block_tokens,
    tracelab_output_block_id,
)
from ..schema import NormalizedCall


def _tokens(block_ids: Iterable[str], token_count: int) -> list[int]:
    values = [token for block in block_ids for token in synthetic_block_tokens(block)]
    if len(values) < token_count:
        raise ValueError(
            f"block reconstruction produced {len(values)} tokens, expected {token_count}"
        )
    return values[:token_count]


def _tracelab_input_blocks(
    call: NormalizedCall,
    contexts: dict[str, list[str]],
) -> list[str]:
    blocks: list[str] = []
    for descriptor in call.prefix_block_ids:
        kind, identity, count = parse_tracelab_descriptor(descriptor)
        if kind == TRACELAB_BOOTSTRAP_PREFIX:
            blocks.extend(
                f"tracelab-bootstrap:{identity}:{index}" for index in range(count)
            )
        elif kind == TRACELAB_REUSE_PREFIX:
            try:
                previous = contexts[identity]
            except KeyError as error:
                raise ValueError(
                    f"request {call.request_id} references unavailable context {identity}"
                ) from error
            if len(previous) < count:
                raise ValueError(
                    f"request {call.request_id} reuses {count} blocks from a "
                    f"{len(previous)}-block context"
                )
            blocks.extend(previous[:count])
        elif kind == TRACELAB_APPEND_PREFIX:
            if identity != call.request_id:
                raise ValueError(
                    f"request {call.request_id} has append descriptor for {identity}"
                )
            blocks.extend(
                f"tracelab-input:{call.request_id}:{index}" for index in range(count)
            )
    return blocks


def _output_tokens(call: NormalizedCall) -> list[int]:
    count = math.ceil(call.output_tokens / 16) if call.output_tokens else 0
    if call.source == "tracelab":
        blocks = [tracelab_output_block_id(call.request_id, index) for index in range(count)]
    else:
        blocks = [f"output:{call.request_id}:{index}" for index in range(count)]
    return _tokens(blocks, call.output_tokens)


def simulator_records(calls: Iterable[NormalizedCall]) -> list[dict]:
    """Return flat open-loop requests and closed-loop agentic sessions."""
    flat: list[tuple[int, dict]] = []
    sessions: dict[str, list[NormalizedCall]] = defaultdict(list)
    for call in calls:
        if call.dependency_mode == "closed_loop":
            sessions[call.session_id].append(call)
            continue
        flat.append(
            (
                call.session_arrival_ns,
                {
                    "request_id": call.request_id,
                    "session_id": call.session_id,
                    "tenant": call.tenant,
                    "input_toks": call.input_tokens,
                    "output_toks": call.output_tokens,
                    "arrival_time_ns": call.session_arrival_ns,
                    "input_tok_ids": _tokens(call.prefix_block_ids, call.input_tokens),
                    "output_tok_ids": _output_tokens(call),
                },
            )
        )

    agentic: list[tuple[int, dict]] = []
    for session_id, session_calls in sessions.items():
        ordered = sorted(session_calls, key=lambda call: (call.step_index, call.request_id))
        indices = [call.step_index for call in ordered]
        if indices != list(range(len(indices))):
            raise ValueError(f"session {session_id} has non-contiguous step indices: {indices}")
        arrival = min(call.session_arrival_ns for call in ordered)
        contexts: dict[str, list[str]] = {}
        sub_requests = []
        for call in ordered:
            input_blocks = _tracelab_input_blocks(call, contexts)
            output_ids = _output_tokens(call)
            output_block_count = math.ceil(call.output_tokens / 16) if call.output_tokens else 0
            output_blocks = [
                tracelab_output_block_id(call.request_id, index)
                for index in range(output_block_count)
            ]
            contexts[call.request_id] = input_blocks + output_blocks
            sub_requests.append(
                {
                    "request_id": call.request_id,
                    "tenant": call.tenant,
                    "input_toks": call.input_tokens,
                    "output_toks": call.output_tokens,
                    "input_tok_ids": _tokens(input_blocks, call.input_tokens),
                    "output_tok_ids": output_ids,
                    "tool_duration_ns": call.tool_delay_after_ns,
                }
            )
        agentic.append(
            (
                arrival,
                {
                    "session_id": session_id,
                    "tenant": "coding_agent",
                    "arrival_time_ns": arrival,
                    "sub_requests": sub_requests,
                },
            )
        )
    return [record for _, record in sorted(flat + agentic, key=lambda item: item[0])]


def write_simulator_jsonl(calls: Iterable[NormalizedCall], path: str | Path) -> None:
    import os

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for record in simulator_records(calls):
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        os.replace(temporary, destination)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
