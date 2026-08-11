import json
from pathlib import Path

import pytest
from llmschedbench.data.normalize import (
    NormalizationError,
    normalize_qwen,
    normalize_tracelab,
)
from llmschedbench.data.simulator import simulator_records
from llmschedbench.prefixes import TRACELAB_REUSE_PREFIX, parse_tracelab_descriptor

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_qwen_preserves_sessions_timestamps_and_source_blocks():
    rows = normalize_qwen(
        FIXTURES / "qwen_traceA_blksz_16.jsonl",
        tenant="chat",
    )

    assert len(rows) == 2
    assert rows[0].tenant == "chat"
    assert rows[0].session_id == rows[1].session_id
    assert rows[1].step_index == 1
    assert rows[1].session_arrival_ns == 250_000_000
    assert rows[0].prefix_block_ids[0] == rows[1].prefix_block_ids[0]
    assert rows[0].prefix_block_ids[1] != rows[1].prefix_block_ids[1]


def test_qwen_rejects_a_missing_parent(tmp_path):
    path = tmp_path / "qwen_traceA_invalid.jsonl"
    path.write_text(
        json.dumps(
            {
                "chat_id": 1,
                "parent_chat_id": 99,
                "timestamp": 0,
                "input_length": 16,
                "output_length": 1,
                "type": "text",
                "turn": 2,
                "hash_ids": [1],
            }
        )
        + "\n"
    )

    with pytest.raises(NormalizationError, match="parent_chat_id 99"):
        normalize_qwen(path, tenant="chat")


def test_qwen_rejects_inconsistent_block_accounting(tmp_path):
    path = tmp_path / "qwen_traceA_invalid.jsonl"
    path.write_text(
        json.dumps(
            {
                "chat_id": 0,
                "parent_chat_id": -1,
                "timestamp": 0,
                "input_length": 17,
                "output_length": 1,
                "type": "text",
                "turn": 1,
                "hash_ids": [1],
            }
        )
        + "\n"
    )

    with pytest.raises(NormalizationError, match="expected 2 hash blocks"):
        normalize_qwen(path, tenant="chat")


def test_tracelab_reconstructs_context_and_tool_wait():
    rows = normalize_tracelab(FIXTURES / "tracelab_round_trace.jsonl")

    assert len(rows) == 2
    assert all(row.tenant == "coding_agent" for row in rows)
    assert all(row.dependency_mode == "closed_loop" for row in rows)
    assert rows[0].tool_delay_after_ns == 500_000_000
    assert len(rows[0].prefix_block_ids) == 2
    kind, identity, count = parse_tracelab_descriptor(rows[1].prefix_block_ids[0])
    assert kind == TRACELAB_REUSE_PREFIX
    assert identity == rows[0].request_id
    assert count == 3

    records = simulator_records(rows)
    assert len(records) == 1
    session = records[0]
    assert session["tenant"] == "coding_agent"
    assert len(session["sub_requests"]) == 2
    first, second = session["sub_requests"]
    assert len(first["input_tok_ids"]) == 32
    assert len(first["output_tok_ids"]) == 16
    assert second["input_tok_ids"] == first["input_tok_ids"] + first["output_tok_ids"]
    assert first["request_id"] == rows[0].request_id
    assert first["tool_duration_ns"] == 500_000_000


def test_tracelab_rejects_invalid_cache_accounting(tmp_path):
    source = FIXTURES / "tracelab_round_trace.jsonl"
    row = json.loads(source.read_text().splitlines()[0])
    row["newly_append_tokens"] = 15
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(row) + "\n")

    with pytest.raises(NormalizationError, match="must equal input_tokens_total"):
        normalize_tracelab(path)
