from llmschedbench import _policy


def test_least_loaded_binding_dispatches_and_rotates_ties():
    policy = _policy.make_policy("least_loaded")
    request = _policy.RequestView()
    request.request_id = "request-1"
    request.session_id = "session-1"
    request.tenant = _policy.Tenant.CHAT
    workers = []
    for worker_id in ("worker-0", "worker-1"):
        worker = _policy.WorkerView()
        worker.worker_id = worker_id
        workers.append(worker)

    policy.enqueue(request)
    policy.enqueue(request)
    assert policy.dispatch(0, workers)[0].worker_id == "worker-0"
    assert policy.dispatch(0, workers)[0].worker_id == "worker-1"


def test_least_loaded_binding_uses_normalized_load_score():
    worker = _policy.WorkerView()
    worker.waiting_requests = 2
    worker.running_requests = 3
    worker.capacity = 11
    assert _policy.least_loaded_score(worker) == 1.0


def test_all_policy_factories_are_bound():
    config = _policy.PolicyConfig()
    config.chat_weight = 1.0
    config.coding_agent_weight = 1.0
    config.api_batch_weight = 0.5
    for name in (
        "least_loaded",
        "cache_max",
        "weighted_fair",
        "slo_guarded_affinity",
    ):
        assert _policy.make_policy(name, config) is not None


def test_slo_guarded_affinity_falls_back_to_fast_worker():
    policy = _policy.make_policy("slo_guarded_affinity")
    request = _policy.RequestView()
    request.request_id = "urgent"
    request.session_id = "session"
    request.tenant = _policy.Tenant.CODING_AGENT
    request.input_tokens = 100
    request.slo_ns = 500_000_000
    policy.enqueue(request)

    warm = _policy.WorkerView()
    warm.worker_id = "warm"
    warm.reusable_prefix_tokens = 100
    warm.estimated_queued_token_work = 100
    warm.decode_tokens_per_second = 100
    warm.prefill_tokens_per_second = 1000
    cold = _policy.WorkerView()
    cold.worker_id = "cold"
    cold.decode_tokens_per_second = 100
    cold.prefill_tokens_per_second = 1000

    decision = policy.dispatch(0, [warm, cold])[0]
    assert decision.request_id == "urgent"
    assert decision.worker_id == "cold"
    assert decision.reason_code == "slo_fallback_min_ttft"
    assert decision.predicted_ttft_seconds == 0.1
