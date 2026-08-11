from llmschedbench.simulator_bridge import PolicyRouter


def _request(request_id, tenant="chat", input_tokens=100, output_tokens=10):
    return {
        "request_id": request_id,
        "session_id": f"session-{request_id}",
        "tenant": tenant,
        "input_toks": input_tokens,
        "output_toks": input_tokens + output_tokens,
        "arrival_time_ns": 0,
        "input_hash_ids": list(range(input_tokens)),
    }


def test_bridge_routes_global_ready_queue_and_updates_snapshot_load():
    router = PolicyRouter("least_loaded")
    workers = [
        {"worker_id": "0", "capacity": 1},
        {"worker_id": "1", "capacity": 1},
    ]

    decisions = router([_request("a"), _request("b")], 0, workers)

    assert [decision["request_id"] for decision in decisions] == ["a", "b"]
    assert [decision["worker_id"] for decision in decisions] == ["0", "1"]
    assert len(router.decisions) == 2


def test_bridge_supplies_per_request_reuse_for_slo_fallback():
    router = PolicyRouter(
        "slo_guarded_affinity",
        tenant_slo_ns={"coding_agent": 500_000_000},
        service_rates={
            "warm": {
                "prefill_tokens_per_second": 1000,
                "decode_tokens_per_second": 100,
            },
            "cold": {
                "prefill_tokens_per_second": 1000,
                "decode_tokens_per_second": 100,
            },
        },
    )
    request = _request("urgent", tenant="coding_agent")
    workers = [
        {
            "worker_id": "warm",
            "capacity": 1,
            "estimated_queued_token_work": 100,
            "reusable_prefix_tokens_by_request": {"urgent": 100},
        },
        {
            "worker_id": "cold",
            "capacity": 1,
            "reusable_prefix_tokens_by_request": {"urgent": 0},
        },
    ]

    decision = router([request], 0, workers)[0]

    assert decision["worker_id"] == "cold"
    assert decision["reason_code"] == "slo_fallback_min_ttft"
    assert decision["predicted_ttft_seconds"] == 0.1
