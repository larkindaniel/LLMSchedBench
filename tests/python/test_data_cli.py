import json
from collections import Counter
from pathlib import Path

from llmschedbench.cli import main
from llmschedbench.schema import read_parquet

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_qwen_normalize_cli_writes_parquet_and_simulator_jsonl(tmp_path, capsys):
    parquet = tmp_path / "qwen.parquet"
    simulator = tmp_path / "qwen.jsonl"

    result = main(
        [
            "data",
            "normalize",
            "qwen",
            "--input",
            str(FIXTURES / "qwen_traceA_blksz_16.jsonl"),
            "--input",
            str(FIXTURES / "qwen_traceB_blksz_16.jsonl"),
            "--output",
            str(parquet),
            "--simulator-output",
            str(simulator),
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["calls"] == 3
    provenance = json.loads(Path(summary["provenance"]).read_text())
    assert provenance["normalization_schema_version"] == "1.0.0"
    assert provenance["outputs"]["parquet"]["rows"] == 3
    rows = read_parquet(str(parquet))
    assert [row.tenant for row in rows] == ["chat", "chat", "api_batch"]
    records = [json.loads(line) for line in simulator.read_text().splitlines()]
    assert len(records) == 3
    assert Counter(row["tenant"] for row in records) == {
        "chat": 2,
        "api_batch": 1,
    }
    assert all(row["request_id"] for row in records)
    assert all(len(row["input_tok_ids"]) == row["input_toks"] for row in records)
