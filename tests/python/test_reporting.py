from llmschedbench.reporting import (
    aggregate_confidence_intervals,
    confidence_interval,
)


def _summary(policy: str, seed: int, ttft: float) -> dict:
    return {
        "spec": {
            "scenario": "balanced",
            "policy": policy,
            "arrival_rate_rps": 1.6,
            "seed": seed,
        },
        "ttft_ms": {"p95": ttft},
        "latency_ms": {"p95": 2 * ttft},
        "slo_attainment_rate": 1.0,
        "cache": {"simulator_prefix_hit_rate": 0.5},
        "fairness": {"jain_weighted_service": 0.9},
        "starvation_rate": 0.1,
        "goodput_rps": 1.2,
        "load": {"average_npu_utilization": 0.8},
        "per_tenant": {
            "coding_agent": {"workflow_completion_ms": {"p95": 1000.0}}
        },
    }


def test_confidence_interval_uses_student_t_margin():
    interval = confidence_interval([1.0, 2.0, 3.0, 4.0, 5.0])

    assert interval["n"] == 5
    assert interval["mean"] == 3.0
    assert interval["lower"] < 3.0 < interval["upper"]
    assert interval["margin"] > 1.0


def test_aggregate_confidence_intervals_groups_seeds_by_policy():
    summaries = [
        _summary("least_loaded", seed, float(seed - 1700))
        for seed in range(1729, 1734)
    ]

    result = aggregate_confidence_intervals(summaries)

    assert result["least_loaded"]["ttft_p95_ms"]["n"] == 5
    assert result["least_loaded"]["cache_hit_rate"]["mean"] == 0.5
