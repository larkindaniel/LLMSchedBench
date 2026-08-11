import json
from pathlib import Path

import pytest
from llmschedbench.experiment import REQUIRED_RUN_OUTPUTS, sha256_file
from llmschedbench.metrics import percentile, summarize_run


def _write_fixture_run(root: Path) -> Path:
    run = root / "balanced-r1-s1729-least-loaded"
    run.mkdir()
    (run / "resolved-scenario.json").write_text(
        json.dumps(
            {
                "name": "balanced",
                "seed": 1729,
                "tenants": {
                    "chat": {"weight": 1.0, "ttft_slo_ms": 1000},
                    "coding_agent": {"weight": 1.0, "ttft_slo_ms": 1500},
                    "api_batch": {"weight": 0.5, "ttft_slo_ms": 10000},
                },
                "metrics": {"starvation_slo_multiplier": 5.0},
            }
        ),
        encoding="utf-8",
    )
    (run / "workload-provenance.json").write_text(
        json.dumps({"entries": 3}), encoding="utf-8"
    )
    request_map = [
        {
            "internal_request_id": index,
            "request_id": f"request-{index}",
            "session_id": f"session-{index}",
            "step_index": 0,
            "tenant": tenant,
            "arrival_time_ns": index * 1_000_000_000,
            "input_tokens": 100,
            "output_tokens": 10,
        }
        for index, tenant in enumerate(("chat", "coding_agent", "api_batch"))
    ]
    (run / "request-map.json").write_text(json.dumps(request_map), encoding="utf-8")
    (run / "workload.jsonl").write_text("{}\n", encoding="utf-8")
    (run / "simulator-results.csv").write_text(
        "instance id,request id,model,input,output,arrival,end_time,latency,queuing_delay,TTFT,TPOT,ITL\n"
        "0,0,m,100,10,0,2500000000,2500000000,0,2000000000,10000000,[]\n"
        "1,1,m,100,10,1000000000,3500000000,2500000000,0,1000000000,10000000,[]\n"
        "0,2,m,100,10,2000000000,2500000000,500000000,0,100000000,10000000,[]\n",
        encoding="utf-8",
    )
    decisions = [
        {
            "admit": True,
            "cache_hit_tokens": 10 if index < 2 else 30,
            "request_id": f"request-{index}",
            "tenant": tenant,
            "worker_id": str(index % 2),
        }
        for index, tenant in enumerate(("chat", "coding_agent", "api_batch"))
    ]
    (run / "decisions.jsonl").write_text(
        "".join(json.dumps(value) + "\n" for value in decisions), encoding="utf-8"
    )
    (run / "simulator.log").write_text(
        "Total clocks (ns): 3000000000\n"
        "NPU prefix hit prompt tokens: 50\n"
        "NPU prefix hit ratio (%): 16.67\n"
        "NPU energy consumption (J): 3.0\n",
        encoding="utf-8",
    )
    outputs = {
        name: {
            "sha256": sha256_file(run / name),
            "size": (run / name).stat().st_size,
        }
        for name in REQUIRED_RUN_OUTPUTS
    }
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "state": "complete",
                "spec": {
                    "scenario": "balanced",
                    "policy": "least_loaded",
                    "arrival_rate_rps": 1.0,
                    "seed": 1729,
                },
                "outputs": outputs,
            }
        ),
        encoding="utf-8",
    )
    return run


def _write_cluster(path: Path) -> None:
    instances = [
        {"hardware": "gpu", "num_npus": 1},
        {"hardware": "gpu", "num_npus": 1},
    ]
    path.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "instances": instances,
                        "power": {
                            "npu": {
                                "gpu": {
                                    "idle_power": 0,
                                    "standby_power": 0,
                                    "active_power": 1,
                                }
                            }
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_percentile_interpolates_and_rejects_invalid_probability():
    assert percentile([0.0, 10.0], 0.95) == pytest.approx(9.5)
    with pytest.raises(ValueError, match="between"):
        percentile([1.0], 1.1)


def test_summary_cross_checks_routing_and_calculates_metrics(tmp_path: Path):
    run = _write_fixture_run(tmp_path)
    cluster = tmp_path / "cluster.json"
    _write_cluster(cluster)

    summary = summarize_run(run, cluster_config=cluster)

    assert summary["requests"] == 3
    assert summary["slo_attainment_rate"] == pytest.approx(2 / 3)
    assert summary["starvation_rate"] == pytest.approx(1 / 3)
    assert summary["goodput_rps"] == pytest.approx(1 / 3)
    assert summary["cache"]["decision_prefill_tokens_avoided"] == 50
    assert summary["cache"]["simulator_prefix_hit_rate"] == pytest.approx(0.1667)
    assert summary["load"]["average_npu_utilization"] == pytest.approx(0.5)


def test_summary_rejects_decision_worker_mismatch(tmp_path: Path):
    run = _write_fixture_run(tmp_path)
    decisions = (run / "decisions.jsonl").read_text(encoding="utf-8")
    decisions = decisions.replace('"worker_id": "0"', '"worker_id": "1"', 1)
    (run / "decisions.jsonl").write_text(decisions, encoding="utf-8")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    path = run / "decisions.jsonl"
    manifest["outputs"]["decisions.jsonl"] = {
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    cluster = tmp_path / "cluster.json"
    _write_cluster(cluster)

    with pytest.raises(ValueError, match="worker mismatch"):
        summarize_run(run, cluster_config=cluster)
