"""Real serving replay. Cache knowledge is a bounded routing estimate, not telemetry."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import shutil
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .experiment import _atomic_json, _checksums, validate_completed_run

GPU_POLICIES = ("least_loaded", "cache_max")


class GPUConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    model_revision: str
    image: str
    endpoints: list[str] = Field(min_length=1)
    host_cuda_driver: bool = False
    max_model_len: int = Field(default=16384, gt=0)
    request_timeout_s: float = Field(default=180, gt=0, allow_inf_nan=False)
    run_timeout_s: float = Field(default=1800, gt=0, allow_inf_nan=False)
    max_inflight: int = Field(default=256, gt=0)
    cache_block_size: int = Field(default=16, gt=0)
    estimated_cache_blocks: int = Field(default=4096, gt=0)

    @model_validator(mode="after")
    def validate_identity(self):
        if len(self.model_revision) != 40 or any(
            c not in "0123456789abcdef" for c in self.model_revision
        ):
            raise ValueError("model_revision must be an immutable 40-character commit")
        if "@sha256:" not in self.image or len(self.image.split("@sha256:")[-1]) != 64:
            raise ValueError("image must be pinned by sha256 digest")
        self.endpoints = [url.rstrip("/") for url in self.endpoints]
        if len(set(self.endpoints)) != len(self.endpoints):
            raise ValueError("worker endpoints must be distinct")
        for endpoint in self.endpoints:
            url = urlsplit(endpoint)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path
            ):
                raise ValueError("endpoints must be server origins without credentials")
        return self


def map_tokens(records: list[dict], vocabulary: list[int], max_len: int) -> list[dict]:
    """Map synthetic identities to real, non-special vocabulary IDs deterministically."""
    if not vocabulary or len(set(vocabulary)) != len(vocabulary):
        raise ValueError("vocabulary must contain unique non-special token IDs")
    result = json.loads(json.dumps(records))
    seen = set()
    for entry in result:
        if int(entry["arrival_time_ns"]) < 0:
            raise ValueError("negative arrival")
        calls = entry.get("sub_requests", [entry])
        if not calls:
            raise ValueError("empty session")
        for call in calls:
            rid = call["request_id"]
            if rid in seen:
                raise ValueError(f"duplicate request ID: {rid}")
            seen.add(rid)
            ids = call["input_tok_ids"]
            n, output = int(call["input_toks"]), int(call["output_toks"])
            if n != len(ids) or n < 1 or output < 1 or n + output > max_len:
                raise ValueError(f"invalid lengths/context overflow: {rid}")
            if int(call.get("tool_duration_ns", 0)) < 0:
                raise ValueError("negative tool delay")
            call["prompt"] = [vocabulary[int(token) % len(vocabulary)] for token in ids]
    if not seen:
        raise ValueError("empty workload")
    return result


def prefix_keys(tokens: list[int], block_size: int) -> list[str]:
    digest = hashlib.sha256()
    keys = []
    for start in range(0, len(tokens) - block_size + 1, block_size):
        digest.update(json.dumps(tokens[start : start + block_size]).encode())
        keys.append(digest.hexdigest())
    return keys


class CacheEstimate:
    def __init__(self, limit: int):
        self.limit = limit
        self.blocks: OrderedDict[str, None] = OrderedDict()

    def reuse(self, keys: list[str]) -> int:
        count = 0
        for key in keys:
            if key not in self.blocks:
                break
            count += 1
        return count

    def remember(self, keys: list[str]) -> None:
        for key in keys:
            self.blocks[key] = None
            self.blocks.move_to_end(key)
        while len(self.blocks) > self.limit:
            self.blocks.popitem(last=False)


async def stream_completion(client, endpoint: str, payload: dict) -> dict:
    """Require terminal SSE and measured usage; never count chunks as tokens."""
    first = None
    usage = None
    done = False
    async with client.stream(
        "POST", endpoint + "/v1/completions", json=payload
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break
            event = json.loads(data)
            if "error" in event:
                raise ValueError("server returned a streaming error")
            if first is None and any(c.get("text") for c in event.get("choices", [])):
                first = time.monotonic_ns()
            if event.get("usage"):
                usage = event["usage"]
    if not done or first is None or usage is None:
        raise ValueError("incomplete stream, no visible output, or missing token usage")
    if (
        usage.get("prompt_tokens") != len(payload["prompt"])
        or usage.get("completion_tokens") != payload["max_tokens"]
    ):
        raise ValueError("server token counts differ from requested trace lengths")
    return {
        "first_token_ns": first,
        "completion_ns": time.monotonic_ns(),
        "usage": usage,
    }


async def replay(
    records: list[dict],
    config: GPUConfig,
    policy_name: str,
    scenario: dict,
    root: Path,
    client,
) -> list[dict]:
    import httpx

    from . import _policy

    if policy_name not in GPU_POLICIES:
        raise ValueError(f"GPU backend currently supports {GPU_POLICIES}")
    policy = _policy.make_policy(policy_name)
    policy.reset(int(scenario["seed"]))
    caches = [CacheEstimate(config.estimated_cache_blocks) for _ in config.endpoints]
    inflight = [0 for _ in config.endpoints]
    rows: list[dict] = []
    origin = time.monotonic_ns()
    tenants = {
        "chat": _policy.Tenant.CHAT,
        "coding_agent": _policy.Tenant.CODING_AGENT,
        "api_batch": _policy.Tenant.API_BATCH,
    }
    with (root / "requests.jsonl").open("w") as output:

        def save(row):
            rows.append(row)
            output.write(json.dumps(row, allow_nan=False) + "\n")
            output.flush()

        async def session(entry):
            scheduled = int(entry["arrival_time_ns"])
            await asyncio.sleep(
                max(0, (origin + scheduled - time.monotonic_ns()) / 1e9)
            )
            failed = False
            calls = entry.get("sub_requests", [entry])
            for step, call in enumerate(calls):
                rid = call["request_id"]
                tenant = call.get("tenant", entry.get("tenant", "chat"))
                row: dict[str, Any] = {
                    "request_id": rid,
                    "session_id": entry.get("session_id", rid),
                    "tenant": tenant,
                    "scheduled_ns": scheduled,
                    "input_tokens": call["input_toks"],
                    "output_tokens": call["output_toks"],
                    "slo_ms": scenario["tenants"][tenant]["ttft_slo_ms"],
                }
                if failed:
                    row.update(status="blocked_dependency", scheduled_ns=None)
                    save(row)
                    continue
                if sum(inflight) >= config.max_inflight:
                    row.update(status="client_overload")
                    save(row)
                    failed = True
                    continue
                request = _policy.RequestView()
                request.request_id = rid
                request.session_id = row["session_id"]
                request.tenant = tenants[tenant]
                request.input_tokens = len(call["prompt"])
                request.output_tokens = call["output_toks"]
                keys = prefix_keys(call["prompt"], config.cache_block_size)
                workers = []
                for index in range(len(config.endpoints)):
                    worker = _policy.WorkerView()
                    worker.worker_id = str(index)
                    worker.running_requests = inflight[index]
                    worker.capacity = 1
                    worker.reusable_prefix_tokens = (
                        caches[index].reuse(keys) * config.cache_block_size
                    )
                    workers.append(worker)
                row["worker_inflight_before"] = list(inflight)
                row["worker_estimated_cache_tokens"] = [
                    worker.reusable_prefix_tokens for worker in workers
                ]
                policy.enqueue(request)
                decision = policy.dispatch(time.monotonic_ns() - origin, workers)[0]
                index = int(decision.worker_id)
                inflight[index] += 1
                dispatch = time.monotonic_ns()
                row.update(
                    worker_id=str(index),
                    dispatch_ns=dispatch - origin,
                    estimated_cache_hit_tokens=decision.cache_hit_tokens,
                    reason=decision.reason_code,
                )
                payload = {
                    "model": config.model,
                    "prompt": call["prompt"],
                    "max_tokens": call["output_toks"],
                    "ignore_eos": True,
                    "temperature": 0,
                    "seed": scenario["seed"],
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                try:
                    async with asyncio.timeout(config.request_timeout_s):
                        measured = await stream_completion(
                            client, config.endpoints[index], payload
                        )
                    row.update(
                        status="ok",
                        usage=measured["usage"],
                        first_token_ns=measured["first_token_ns"] - origin,
                        completion_ns=measured["completion_ns"] - origin,
                    )
                    caches[index].remember(keys)
                except asyncio.CancelledError:
                    row.update(
                        status="cancelled", completion_ns=time.monotonic_ns() - origin
                    )
                    save(row)
                    raise
                except (httpx.HTTPError, ValueError, TimeoutError) as error:
                    row.update(
                        status="error",
                        error_type=type(error).__name__,
                        completion_ns=time.monotonic_ns() - origin,
                    )
                    failed = True
                finally:
                    inflight[index] -= 1
                    policy.on_complete(rid)
                save(row)
                scheduled = row["completion_ns"] + int(call.get("tool_duration_ns", 0))
                if not failed and step + 1 < len(calls):
                    await asyncio.sleep(
                        max(0, (origin + scheduled - time.monotonic_ns()) / 1e9)
                    )

        async with asyncio.timeout(config.run_timeout_s):
            async with asyncio.TaskGroup() as tasks:
                for entry in records:
                    tasks.create_task(session(entry))
    return rows


def summarize(rows: list[dict], window_s: float) -> dict:
    good = [r for r in rows if r["status"] == "ok"]

    def percentile(values, p):
        if not values:
            return None
        values = sorted(values)
        at = (len(values) - 1) * p
        lo = math.floor(at)
        return values[lo] + (values[math.ceil(at)] - values[lo]) * (at - lo)

    ttft = [(r["first_token_ns"] - r["scheduled_ns"]) / 1e6 for r in good]
    latency = [(r["completion_ns"] - r["scheduled_ns"]) / 1e6 for r in good]
    attainment = [r for r, t in zip(good, ttft) if t <= r["slo_ms"]]
    return {
        "calls": len(rows),
        "successful_calls": len(good),
        "unsuccessful_calls": len(rows) - len(good),
        "p95_ttft_ms": percentile(ttft, 0.95),
        "p95_latency_ms": percentile(latency, 0.95),
        "slo_attainment_all_calls": len(attainment) / len(rows) if rows else 0,
        "window_s": window_s,
        "slo_goodput_calls_s": sum(
            r["completion_ns"] <= window_s * 1e9 for r in attainment
        )
        / window_s,
        "max_dispatch_lag_ms": max(
            (
                (r["dispatch_ns"] - r["scheduled_ns"]) / 1e6
                for r in rows
                if "dispatch_ns" in r
            ),
            default=None,
        ),
    }


async def execute_gpu(
    workload: Path,
    config: GPUConfig,
    policy: str,
    root: Path,
    *,
    client=None,
    vocabulary=None,
) -> Path:
    """Create an exclusive run directory; retain failure artifacts instead of overwriting."""
    import httpx

    if policy not in GPU_POLICIES:
        raise ValueError(f"GPU backend currently supports {GPU_POLICIES}")
    if root.exists():
        raise FileExistsError(f"immutable run already exists: {root}")
    scenario = json.loads((workload / "resolved-scenario.json").read_text())
    if scenario["cluster"]["model"] != config.model:
        raise ValueError("scenario and serving model differ")
    if int(scenario["cluster"]["workers"]) != len(config.endpoints):
        raise ValueError("scenario worker count and endpoints differ")
    if vocabulary is None:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            config.model, revision=config.model_revision
        )
        vocabulary = sorted(
            set(tokenizer.get_vocab().values()) - set(tokenizer.all_special_ids)
        )
    records = map_tokens(
        [
            json.loads(line)
            for line in (workload / "workload.jsonl").read_text().splitlines()
            if line
        ],
        vocabulary,
        config.max_model_len,
    )
    root.mkdir(parents=True, exist_ok=False)
    manifest = {
        "backend": "vllm",
        "schema_version": 1,
        "policy": policy,
        "state": "running",
        "cache_estimate": "completed-prompt LRU; no actual eviction visibility",
        "ttft_definition": "scheduled release to first nonempty streamed text",
        "agent_semantics": "synthetic prompts; generated outputs are not fed forward",
    }
    _atomic_json(root / "manifest.json", manifest)
    for name in (
        "resolved-scenario.json",
        "workload.jsonl",
        "workload-provenance.json",
    ):
        if (workload / name).exists():
            shutil.copyfile(workload / name, root / name)
    _atomic_json(root / "gpu-config.json", config.model_dump())
    _atomic_json(root / "token-workload.json", records)
    _atomic_json(root / "vocabulary.json", vocabulary)
    owned = client is None
    if owned:
        client = httpx.AsyncClient(
            timeout=config.request_timeout_s,
            trust_env=False,
            limits=httpx.Limits(max_connections=config.max_inflight + 4),
        )
    try:
        for index, endpoint in enumerate(config.endpoints):
            health = await client.get(endpoint + "/health")
            health.raise_for_status()
            models = await client.get(endpoint + "/v1/models")
            models.raise_for_status()
            if config.model not in {m["id"] for m in models.json()["data"]}:
                raise ValueError("worker does not advertise configured model")
            _atomic_json(root / f"worker-{index}-models.json", models.json())
            reset = await client.post(endpoint + "/reset_prefix_cache")
            reset.raise_for_status()
            if reset.text.strip() == "false":
                raise ValueError("worker refused cache reset")
            metrics = await client.get(endpoint + "/metrics")
            metrics.raise_for_status()
            (root / f"worker-{index}-before.prom").write_text(metrics.text)
        rows = await replay(records, config, policy, scenario, root, client)
        for index, endpoint in enumerate(config.endpoints):
            metrics = await client.get(endpoint + "/metrics")
            metrics.raise_for_status()
            (root / f"worker-{index}-after.prom").write_text(metrics.text)
        window = scenario["trace"]["entries"] / scenario["trace"]["arrival_rate_rps"]
        summary = summarize(rows, window)
        summary["by_tenant"] = {
            t: summarize([r for r in rows if r["tenant"] == t], window)
            for t in scenario["tenants"]
        }
        _atomic_json(root / "summary.json", summary)
        manifest["state"] = (
            "complete" if not summary["unsuccessful_calls"] else "failed"
        )
    except BaseException as error:
        manifest.update(state="failed", error_type=type(error).__name__)
        raise
    finally:
        manifest["outputs"] = _checksums(
            [p for p in root.iterdir() if p.is_file() and p.name != "manifest.json"],
            root,
        )
        _atomic_json(root / "manifest.json", manifest)
        if owned:
            await client.aclose()
    return root


def gpu_report(paths: list[Path], output: Path) -> None:
    lines = [
        "# Real-GPU routing comparison",
        "",
        (
            "TTFT is scheduled release to first nonempty streamed text. "
            "Cache estimates are not measured hits. Single runs are descriptive."
        ),
        "",
        "| Run | Policy | Calls | p95 TTFT ms | p95 latency ms | SLO goodput/s |",
        "|---|---|---:|---:|---:|---:|",
    ]
    identity = None
    for path in paths:
        manifest = validate_completed_run(
            path,
            required_outputs=(
                "summary.json",
                "requests.jsonl",
                "gpu-config.json",
                "token-workload.json",
            ),
        )
        if manifest.get("backend") != "vllm":
            raise ValueError("not a GPU run")
        current = tuple(
            manifest["outputs"][name]["sha256"]
            for name in (
                "gpu-config.json",
                "token-workload.json",
                "resolved-scenario.json",
            )
        )
        if identity is not None and current != identity:
            raise ValueError("comparison requires matching configuration and workload")
        identity = current
        summary = json.loads((path / "summary.json").read_text())
        lines.append(
            f"| {path.name} | {manifest['policy']} | {summary['calls']} | "
            f"{summary['p95_ttft_ms']:.2f} | {summary['p95_latency_ms']:.2f} | "
            f"{summary['slo_goodput_calls_s']:.3f} |"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n")
