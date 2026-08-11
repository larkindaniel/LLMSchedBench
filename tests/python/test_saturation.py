import json

import pytest
from llmschedbench.saturation import (
    parse_simulator_utilization,
    rescale_workload_arrivals,
)


def _meter_config(path):
    path.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "instances": [
                            {"hardware": "meter", "num_npus": 1},
                            {"hardware": "meter", "num_npus": 1},
                        ],
                        "power": {
                            "npu": {
                                "meter": {
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


def test_parse_utilization_from_unit_power_meter(tmp_path):
    cluster = tmp_path / "cluster.json"
    _meter_config(cluster)
    log = tmp_path / "simulator.log"
    log.write_text(
        "Total clocks (ns): 10000000000\n"
        "├─ NPU energy consumption (J): 18.00\n",
        encoding="utf-8",
    )
    value = parse_simulator_utilization(log, cluster)
    assert value["duration_seconds"] == 10
    assert value["average_npu_utilization"] == pytest.approx(0.9)


def test_rescale_preserves_entry_order_and_closed_loop_data(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(
        '{"arrival_time_ns":20,"request_id":"b","sub_requests":[{"x":1}]}\n'
        '{"arrival_time_ns":10,"request_id":"a"}\n',
        encoding="utf-8",
    )
    output = tmp_path / "output.jsonl"
    assert rescale_workload_arrivals(source, output, arrival_rate_rps=2) == 2
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [row["request_id"] for row in records] == ["a", "b"]
    assert [row["arrival_time_ns"] for row in records] == [0, 500_000_000]
    assert records[1]["sub_requests"] == [{"x": 1}]
