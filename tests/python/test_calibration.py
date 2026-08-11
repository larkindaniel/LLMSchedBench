import csv
import json

import pytest
from llmschedbench.calibration import (
    build_unloaded_workload,
    count_cluster_workers,
    estimate_service_rates,
    write_calibration,
    write_unloaded_workload,
)
from llmschedbench.simulator_runner import _load_service_rates


def _write_results(path, *, overlap=False):
    fields = [
        "instance id",
        "request id",
        "model",
        "input",
        "output",
        "arrival",
        "end_time",
        "latency",
        "queuing_delay",
        "TTFT",
        "TPOT",
        "ITL",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        request_id = 0
        for worker_id in range(2):
            for input_tokens in (128, 512, 2048, 4096):
                ttft_ns = 2_000_000 + input_tokens * 1_000
                arrival_ns = 1 if overlap else request_id * 10_000_000_000 + 1
                latency_ns = ttft_ns + 10_000
                writer.writerow(
                    {
                        "instance id": worker_id,
                        "request id": request_id,
                        "model": "fixture",
                        "input": input_tokens,
                        "output": 2,
                        "arrival": arrival_ns,
                        "end_time": arrival_ns + latency_ns,
                        "latency": latency_ns,
                        "queuing_delay": 1_000_000,
                        "TTFT": ttft_ns,
                        "TPOT": 10_000,
                        "ITL": "[]",
                    }
                )
                request_id += 1
            for output_tokens in (32, 64, 128, 256):
                remaining = output_tokens - 1
                ttft_ns = 2_128_000
                decode_ns = 3_000_000 + remaining * 10_000
                arrival_ns = 1 if overlap else request_id * 10_000_000_000 + 1
                latency_ns = ttft_ns + decode_ns
                writer.writerow(
                    {
                        "instance id": worker_id,
                        "request id": request_id,
                        "model": "fixture",
                        "input": 128,
                        "output": output_tokens,
                        "arrival": arrival_ns,
                        "end_time": arrival_ns + latency_ns,
                        "latency": latency_ns,
                        "queuing_delay": 1_000_000,
                        "TTFT": ttft_ns,
                        "TPOT": decode_ns // remaining,
                        "ITL": "[]",
                    }
                )
                request_id += 1


def test_workload_is_balanced_for_round_robin_workers(tmp_path):
    cluster = tmp_path / "cluster.json"
    cluster.write_text(
        json.dumps({"nodes": [{"instances": [{}, {}]}]}), encoding="utf-8"
    )
    assert count_cluster_workers(cluster) == 2
    requests = build_unloaded_workload(workers=2)
    assert len(requests) == 16
    assert requests[0]["arrival_time_ns"] == 1
    assert requests[1]["arrival_time_ns"] == 30_000_000_001
    workload = tmp_path / "calibration.jsonl"
    assert write_unloaded_workload(requests, workload) == 16
    assert len(workload.read_text().splitlines()) == 16


def test_estimator_fits_prefill_and_decode_rates(tmp_path):
    results = tmp_path / "results.csv"
    _write_results(results)
    value = estimate_service_rates(results, expected_workers=2)
    assert value["schema_version"] == "1.0.0"
    assert value["source"]["simulator_results"]["rows"] == 16
    assert value["workers"]["0"]["prefill_tokens_per_second"] == pytest.approx(
        1_000_000
    )
    assert value["workers"]["1"]["decode_tokens_per_second"] == pytest.approx(
        100_000
    )
    assert value["diagnostics"]["0"]["prefill"]["r_squared"] == pytest.approx(1)
    calibration = tmp_path / "rates.json"
    write_calibration(value, calibration)
    assert _load_service_rates(calibration) == value["workers"]


def test_estimator_rejects_overlapping_measurements(tmp_path):
    results = tmp_path / "results.csv"
    _write_results(results, overlap=True)
    with pytest.raises(ValueError, match="not unloaded"):
        estimate_service_rates(results)


def test_rate_loader_rejects_non_positive_values(tmp_path):
    rates = tmp_path / "rates.json"
    rates.write_text(
        json.dumps(
            {
                "workers": {
                    "0": {
                        "prefill_tokens_per_second": 1,
                        "decode_tokens_per_second": 0,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be positive"):
        _load_service_rates(rates)
