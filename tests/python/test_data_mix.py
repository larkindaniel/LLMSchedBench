import json
from pathlib import Path

from llmschedbench.data.mix import allocate_entries, build_mixed_trace
from llmschedbench.data.normalize import normalize_qwen, normalize_tracelab
from llmschedbench.schema import read_parquet, write_parquet

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_allocate_entries_uses_largest_remainders():
    assert allocate_entries(
        7,
        {"chat": 0.60, "coding_agent": 0.25, "api_batch": 0.15},
    ) == {"chat": 4, "coding_agent": 2, "api_batch": 1}


def test_mixed_trace_is_exact_and_deterministic(tmp_path):
    qwen = tmp_path / "qwen.parquet"
    tracelab = tmp_path / "tracelab.parquet"
    qwen_calls = normalize_qwen(
        FIXTURES / "qwen_traceA_blksz_16.jsonl",
        tenant="chat",
    ) + normalize_qwen(
        FIXTURES / "qwen_traceB_blksz_16.jsonl",
        tenant="api_batch",
    )
    write_parquet(qwen_calls, str(qwen))
    write_parquet(
        normalize_tracelab(FIXTURES / "tracelab_round_trace.jsonl"),
        str(tracelab),
    )
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "name": "fixture-mix",
                "seed": 1729,
                "cluster": {"workers": 2},
                "tenants": {
                    "chat": {},
                    "coding_agent": {},
                    "api_batch": {},
                },
                "traffic": {
                    "chat": 1 / 3,
                    "coding_agent": 1 / 3,
                    "api_batch": 1 / 3,
                },
                "trace": {
                    "entries": 3,
                    "arrival_rate_rps": 1.0,
                    "coding_session": {
                        "min_steps": 2,
                        "max_steps": 2,
                        "max_input_tokens": 64,
                    },
                },
            }
        )
    )

    first_parquet = tmp_path / "first.parquet"
    first_jsonl = tmp_path / "first.jsonl"
    first = build_mixed_trace(
        scenario,
        qwen,
        tracelab,
        output_path=first_parquet,
        simulator_output_path=first_jsonl,
    )
    second_parquet = tmp_path / "second.parquet"
    second_jsonl = tmp_path / "second.jsonl"
    second = build_mixed_trace(
        scenario,
        qwen,
        tracelab,
        output_path=second_parquet,
        simulator_output_path=second_jsonl,
    )

    assert first["allocations"] == {
        "chat": 1,
        "coding_agent": 1,
        "api_batch": 1,
    }
    assert first["realized_entries"] == first["allocations"]
    assert first["entries"] == 3
    assert first["calls"] == 4
    assert {row.tenant for row in read_parquet(str(first_parquet))} == {
        "chat",
        "coding_agent",
        "api_batch",
    }
    assert first_parquet.read_bytes() == second_parquet.read_bytes()
    assert first_jsonl.read_bytes() == second_jsonl.read_bytes()
    assert first["outputs"]["parquet"]["sha256"] == second["outputs"]["parquet"][
        "sha256"
    ]
