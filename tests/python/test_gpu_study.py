import asyncio
import json

import httpx
import pytest
from llmschedbench.gpu import GPUConfig, map_tokens, prefix_keys
from llmschedbench.gpu_study import CONDITIONS, metrics, prepare, records, report


def test_conditions_are_matched_and_have_intended_prefix_sharing():
    conditions = {c: records(c, 1729, 192, 4) for c in CONDITIONS}
    baseline = conditions["low_reuse"]
    for rows in conditions.values():
        assert [
            (r["arrival_time_ns"], r["tenant"], r["input_toks"], r["output_toks"])
            for r in rows
        ] == [
            (r["arrival_time_ns"], r["tenant"], r["input_toks"], r["output_toks"])
            for r in baseline
        ]
        assert [r["input_tok_ids"][3072:] for r in rows] == [
            r["input_tok_ids"][3072:] for r in baseline
        ]
    assert len({tuple(r["input_tok_ids"][:16]) for r in baseline}) == 192
    assert (
        len({tuple(r["input_tok_ids"][:3072]) for r in conditions["distributed"]}) == 8
    )
    from collections import Counter

    counts = Counter(tuple(r["input_tok_ids"][:3072]) for r in conditions["hot_prefix"])
    assert counts.most_common(1)[0][1] > 130
    mapped = map_tokens(baseline, list(range(151000)), 16384)
    assert len({prefix_keys(r["prompt"], 16)[0] for r in mapped}) == 192
    assert baseline == records("low_reuse", 1729, 192, 4)


def test_prometheus_sums_worker_label_series_and_ignores_histograms():
    assert metrics(
        '# HELP ignored\nvllm:prefix_cache_hits_total{model_name="x",engine="0"} 10\nvllm:prefix_cache_hits_total{model_name="x",engine="1"} 12\nvllm:num_requests_waiting 3\n'
    ) == {"vllm:prefix_cache_hits_total": 22, "vllm:num_requests_waiting": 3}


def test_full_study_run_measurement_and_report(tmp_path, monkeypatch):
    from llmschedbench import gpu_study
    from llmschedbench.gpu import execute_gpu

    source = prepare(tmp_path / "workload", "distributed", 1729, 2, 100, "test")
    config = GPUConfig(
        model="test",
        model_revision="a" * 40,
        image="test@sha256:" + "b" * 64,
        endpoints=["http://w0", "http://w1"],
    )

    async def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "test"}]})
        if request.url.path == "/v1/completions":
            p = json.loads(request.content)
            return httpx.Response(
                200,
                text="data: "
                + json.dumps(
                    {
                        "choices": [{"text": "hi"}],
                        "usage": {
                            "prompt_tokens": len(p["prompt"]),
                            "completion_tokens": 128,
                        },
                    }
                )
                + "\n\ndata: [DONE]\n\n",
            )
        if request.url.path == "/metrics":
            return httpx.Response(
                200,
                text="vllm:prefix_cache_hits_total 0\nvllm:prefix_cache_queries_total 0\n",
            )
        return httpx.Response(200, text="")

    async def fake_execute(*args, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await execute_gpu(*args, client=client, **kwargs)

    async def fake_snapshot(config):
        return [
            {"vllm:prefix_cache_hits_total": 16, "vllm:prefix_cache_queries_total": 32}
        ] * 2

    async def fake_sample(config, path, stop):
        path.write_text(
            json.dumps({"workers": [{"vllm:num_requests_waiting": 2}, {}]}) + "\n"
        )
        await stop.wait()

    monkeypatch.setattr(gpu_study, "execute_gpu", fake_execute)
    monkeypatch.setattr(gpu_study, "snapshot", fake_snapshot)
    monkeypatch.setattr(gpu_study, "sample", fake_sample)
    root = tmp_path / "study/runs/distributed-s1729-least_loaded"
    asyncio.run(
        gpu_study.measured(source, config, "least_loaded", root, list(range(151000)))
    )
    report(tmp_path / "study", tmp_path / "report")
    r = json.loads((tmp_path / "report/results.json").read_text())[0]
    assert r["successful_calls"] == 2
    assert r["actual_cache_hit_fraction"] == 0.5
    assert r["peak_waiting"] == [2, 0]
    assert sum(r["worker_calls"]) == 2
    (root / "requests.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="mismatch"):
        report(tmp_path / "study", tmp_path / "report")
