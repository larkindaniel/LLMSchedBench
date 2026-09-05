import asyncio
import json

import httpx
import pytest
from llmschedbench.gpu import (
    CacheEstimate,
    GPUConfig,
    execute_gpu,
    gpu_report,
    map_tokens,
    prefix_keys,
)


def config(**kwargs):
    return GPUConfig(
        model="test",
        model_revision="a" * 40,
        image="vllm/vllm-openai@sha256:" + "b" * 64,
        endpoints=["http://worker0", "http://worker1"],
        **kwargs,
    )


def call(rid, **kwargs):
    return dict(
        request_id=rid,
        tenant="chat",
        input_toks=32,
        output_toks=2,
        input_tok_ids=list(range(32)),
        **kwargs,
    )


def workload(path, records):
    path.mkdir()
    (path / "workload.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    (path / "resolved-scenario.json").write_text(
        json.dumps(
            {
                "name": "test",
                "seed": 1,
                "cluster": {"model": "test", "workers": 2},
                "trace": {"entries": len(records), "arrival_rate_rps": 10},
                "tenants": {"chat": {"ttft_slo_ms": 1000}},
            }
        )
    )
    return path


class MockWorkers:
    def __init__(self, fail=False):
        self.fail = fail
        self.requests = []

    async def __call__(self, request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "test"}]})
        if request.url.path == "/v1/completions":
            self.requests.append(request)
            if self.fail:
                return httpx.Response(200, text='data: {"choices": []}\n\n')
            payload = json.loads(request.content)
            assert payload["ignore_eos"] is True
            assert max(payload["prompt"]) < 100
            await asyncio.sleep(0.005)
            usage = {"prompt_tokens": len(payload["prompt"]), "completion_tokens": 2}
            events = [
                {"choices": [{"text": ""}]},
                {"choices": [{"text": "hello"}]},
                {"choices": [], "usage": usage},
            ]
            text = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
            return httpx.Response(200, text=text + "data: [DONE]\n\n")
        return httpx.Response(200, text="")


def run(tmp_path, records, policy="cache_max", fail=False, **kwargs):
    source = workload(tmp_path / "input", records)
    mock = MockWorkers(fail)

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as client:
            await execute_gpu(
                source,
                config(**kwargs),
                policy,
                tmp_path / "run",
                client=client,
                vocabulary=list(range(100)),
            )

    asyncio.run(execute())
    rows = [
        json.loads(line)
        for line in (tmp_path / "run/requests.jsonl").read_text().splitlines()
    ]
    return rows, mock


def test_mapping_preserves_prefix_and_rejects_invalid_lengths():
    records = [call("a", arrival_time_ns=0), call("b", arrival_time_ns=1)]
    mapped = map_tokens(records, [10, 11, 12], 100)
    assert mapped[0]["prompt"] == mapped[1]["prompt"]
    assert set(mapped[0]["prompt"]) <= {10, 11, 12}
    assert "prompt" not in records[0]
    with pytest.raises(ValueError, match="overflow"):
        map_tokens(records, [10], 32)
    with pytest.raises(ValueError, match="duplicate"):
        map_tokens([records[0], records[0]], [10], 100)


def test_cache_is_prefix_sensitive_and_bounded():
    cache = CacheEstimate(2)
    keys = prefix_keys(list(range(32)), 16)
    cache.remember(keys)
    assert cache.reuse(keys) == 2
    assert cache.reuse(prefix_keys([999] + list(range(1, 32)), 16)) == 0
    cache.remember(["other"])
    assert cache.reuse(keys) == 0


@pytest.mark.parametrize("policy", ["least_loaded", "cache_max"])
def test_real_policy_routing_and_report(tmp_path, policy):
    rows, _ = run(
        tmp_path,
        [call("a", arrival_time_ns=0), call("b", arrival_time_ns=30_000_000)],
        policy,
    )
    assert all(r["status"] == "ok" for r in rows)
    assert rows[1]["worker_id"] == ("0" if policy == "cache_max" else "1")
    assert rows[1]["estimated_cache_hit_tokens"] == (32 if policy == "cache_max" else 0)
    gpu_report([tmp_path / "run"], tmp_path / "report.md")
    assert policy in (tmp_path / "report.md").read_text()
    (tmp_path / "run/requests.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="mismatch"):
        gpu_report([tmp_path / "run"], tmp_path / "report.md")


def test_dependency_delay_and_failure(tmp_path):
    records = [
        {
            "session_id": "session",
            "arrival_time_ns": 0,
            "sub_requests": [call("a", tool_duration_ns=20_000_000), call("b")],
        }
    ]
    rows, _ = run(tmp_path, records)
    assert rows[1]["dispatch_ns"] >= rows[0]["completion_ns"] + 20_000_000


def test_incomplete_stream_blocks_dependents_and_fails_manifest(tmp_path):
    records = [
        {
            "session_id": "session",
            "arrival_time_ns": 0,
            "sub_requests": [call("a"), call("b")],
        }
    ]
    rows, mock = run(tmp_path, records, fail=True)
    assert [r["status"] for r in rows] == ["error", "blocked_dependency"]
    assert len(mock.requests) == 1
    assert json.loads((tmp_path / "run/manifest.json").read_text())["state"] == "failed"


def test_overload_is_recorded_not_silently_throttled(tmp_path):
    rows, mock = run(
        tmp_path,
        [call("a", arrival_time_ns=0), call("b", arrival_time_ns=0)],
        max_inflight=1,
    )
    assert {r["status"] for r in rows} == {"ok", "client_overload"}
    assert len(mock.requests) == 1


def test_unpinned_config_rejected():
    with pytest.raises(ValueError, match="immutable"):
        GPUConfig(
            model="test", model_revision="main", image="latest", endpoints=["http://w"]
        )


def test_request_deadline_is_retained_as_failure(tmp_path):
    rows, _ = run(tmp_path, [call("a", arrival_time_ns=0)], request_timeout_s=0.001)
    assert rows[0]["status"] == "error"
    assert rows[0]["error_type"] == "TimeoutError"


def test_run_deadline_retains_cancelled_artifacts(tmp_path):
    with pytest.raises(TimeoutError):
        run(tmp_path, [call("a", arrival_time_ns=0)], run_timeout_s=0.001)
    manifest = json.loads((tmp_path / "run/manifest.json").read_text())
    assert manifest["state"] == "failed"
    assert "requests.jsonl" in manifest["outputs"]
    rows = [
        json.loads(line)
        for line in (tmp_path / "run/requests.jsonl").read_text().splitlines()
    ]
    assert rows[0]["status"] == "cancelled"


def test_stream_measures_first_content_before_completion():
    from llmschedbench.gpu import stream_completion

    class DelayedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"text":""}]}\n\n'
            await asyncio.sleep(0.01)
            yield b'data: {"choices":[{"text":"hello"}]}\n\n'
            await asyncio.sleep(0.02)
            yield b'data: {"usage":{"prompt_tokens":1,"completion_tokens":2}}\n\n'
            yield b"data: [DONE]\n\n"

    async def execute():
        async def handler(request):
            return httpx.Response(200, stream=DelayedStream())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await stream_completion(
                client, "http://worker", {"prompt": [10], "max_tokens": 2}
            )

    measured = asyncio.run(execute())
    assert measured["completion_ns"] - measured["first_token_ns"] >= 20_000_000


def test_usage_mismatch_rejected():
    from llmschedbench.gpu import stream_completion

    async def execute():
        async def handler(request):
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"text":"hi"}]}\n\n'
                    'data: {"usage":{"prompt_tokens":1,"completion_tokens":99}}\n\n'
                    "data: [DONE]\n\n"
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await stream_completion(
                client, "http://worker", {"prompt": [10], "max_tokens": 2}
            )

    with pytest.raises(ValueError, match="token counts"):
        asyncio.run(execute())
