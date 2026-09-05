"""Bounded, paired prefix-affinity experiment. No cloud provisioning."""

import argparse
import asyncio
import json
import random
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .experiment import _atomic_json, _checksums, validate_completed_run
from .gpu import GPUConfig, execute_gpu

CONDITIONS = ("low_reuse", "distributed", "hot_prefix")
SEEDS = (1729, 2718, 3141)
PROTOCOL = {
    "input_tokens": 4096,
    "output_tokens": 128,
    "shared_prefix_tokens": 3072,
    "groups": 8,
    "hot_group_probability": 0.8,
    "probe_rates": [2, 4, 8, 16],
    "probe_calls": 96,
    "probe_p95_threshold_ms": 1000,
    "study_calls": 192,
    "seeds": SEEDS,
    "conditions": CONDITIONS,
    "selection": "First low-reuse least-loaded probe with p95 >= 1000ms; otherwise highest tested rate. Abort on failed calls.",
    "ordering": "Seed-major; rotate conditions by seed index; alternate policy order by pair index.",
    "warmup": "Eight disjoint 4096-input/128-output requests per pair, then execute_gpu resets prefix caches before each measured run.",
    "semantics": "Synthetic independent open-loop requests; fixed lengths; no agent chains. Tenant labels share identical service distributions.",
    "termination": "No run starts unless its worst-case timeout plus 120 seconds fits before the experiment stop time. Failed calls stop the study.",
}


def records(condition, seed, count, rate, input_tokens=4096, prefix_tokens=3072):
    if condition not in CONDITIONS or rate <= 0 or count < 1:
        raise ValueError("invalid workload parameters")
    # Independent RNGs keep arrival times and suffixes identical across conditions.
    arrivals, groups = random.Random(seed), random.Random(seed + 1)
    prefixes = [None] * 8
    for g in range(8):
        rng = random.Random(seed * 100 + g)
        prefixes[g] = [rng.randrange(100, 100000) for _ in range(prefix_tokens)]
    at = 0.0
    out = []
    for i in range(count):
        if i:
            at += arrivals.expovariate(rate)
        rng = random.Random(seed * 100000 + i)
        unique = [rng.randrange(100, 100000) for _ in range(input_tokens)]
        # Two-token identity makes each low-reuse request's first block unique.
        unique[:2] = [100 + i, 100 + seed]
        group = groups.randrange(8)
        if condition == "hot_prefix":
            group = 0 if groups.random() < 0.8 else 1 + groups.randrange(7)
        prompt = (
            unique
            if condition == "low_reuse"
            else prefixes[group] + unique[prefix_tokens:]
        )
        out.append(
            {
                "request_id": f"r{i}",
                "tenant": ("chat", "coding_agent", "api_batch")[i % 3],
                "arrival_time_ns": round(at * 1e9),
                "input_toks": input_tokens,
                "output_toks": 128,
                "input_tok_ids": prompt,
            }
        )
    return out


def prepare(path, condition, seed, count, rate, model):
    path.mkdir(parents=True, exist_ok=False)
    data = records(condition, seed, count, rate)
    scenario = {
        "name": condition,
        "seed": seed,
        "cluster": {"workers": 2, "model": model},
        "trace": {"entries": count, "arrival_rate_rps": rate},
        "tenants": {
            t: {"ttft_slo_ms": s}
            for t, s in [("chat", 1000), ("coding_agent", 1500), ("api_batch", 10000)]
        },
    }
    _atomic_json(path / "resolved-scenario.json", scenario)
    (path / "workload.jsonl").write_text("".join(json.dumps(r) + "\n" for r in data))
    _atomic_json(
        path / "workload-provenance.json",
        {
            "generator": "gpu_study.records",
            "condition": condition,
            "seed": seed,
            "synthetic": True,
        },
    )
    return path


def metrics(text):
    result = {}
    for line in text.splitlines():
        match = re.match(r"^(vllm:[a-z_]+)(?:\{[^}]*\})?\s+([0-9.eE+\-]+)$", line)
        if match:
            name, value = match.groups()
            result[name] = result.get(name, 0) + float(value)
    return result


async def snapshot(config):
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        values = []
        for endpoint in config.endpoints:
            r = await client.get(endpoint + "/metrics")
            r.raise_for_status()
            values.append(metrics(r.text))
        return values


async def sample(config, path, stop):
    start = time.monotonic()
    with path.open("w") as f:
        while not stop.is_set():
            row = {"elapsed_s": time.monotonic() - start}
            try:
                row["workers"] = await snapshot(config)
                proc = await asyncio.create_subprocess_exec(
                    "nvidia-smi",
                    "--query-gpu=index,utilization.gpu,memory.used",
                    "--format=csv,noheader,nounits",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), 5)
                row["gpu_csv"] = stdout.decode().strip()
            except (httpx.HTTPError, OSError, TimeoutError, ValueError) as e:
                row["error"] = type(e).__name__
            f.write(json.dumps(row) + "\n")
            f.flush()
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except TimeoutError:
                pass


async def measured(workload, config, policy, root, vocabulary):
    root.parent.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    telemetry = root.parent / (root.name + "-telemetry.jsonl")
    task = asyncio.create_task(sample(config, telemetry, stop))
    try:
        await execute_gpu(workload, config, policy, root, vocabulary=vocabulary)
        # vLLM's counters may be updated asynchronously after final responses.
        await asyncio.sleep(3)
        after = await snapshot(config)
        _atomic_json(root.parent / (root.name + "-final-metrics.json"), after)
    finally:
        stop.set()
        await task
    validate_completed_run(root, required_outputs=("summary.json", "requests.jsonl"))
    summary = json.loads((root / "summary.json").read_text())
    if summary["unsuccessful_calls"]:
        raise RuntimeError(
            "Failed calls: stop instead of carrying server work into another run"
        )
    return summary


async def run(args):
    from transformers import AutoTokenizer

    config = GPUConfig.model_validate_json(args.config.read_text()).model_copy(
        update={"run_timeout_s": 240, "request_timeout_s": 180}
    )
    if len(config.endpoints) != 2:
        raise ValueError("study requires two independent GPU workers")
    args.output.mkdir(parents=True, exist_ok=False)
    _atomic_json(args.output / "protocol.json", PROTOCOL)
    tokenizer = AutoTokenizer.from_pretrained(
        config.model, revision=config.model_revision
    )
    vocabulary = sorted(
        set(tokenizer.get_vocab().values()) - set(tokenizer.all_special_ids)
    )
    _atomic_json(args.output / "vocabulary.json", vocabulary)
    stop_epoch = datetime.fromisoformat(args.stop_at).timestamp()

    def enough_time():
        if time.time() + 360 >= stop_epoch:
            raise TimeoutError(
                "Preserving download/termination time; no further run launched"
            )

    async def warmup(label):
        enough_time()
        w = prepare(
            args.output / "workloads" / label, "low_reuse", 99991, 8, 8, config.model
        )
        await measured(
            w, config, "least_loaded", args.output / "warmups" / label, vocabulary
        )

    await warmup("initial")
    probes = []
    selected = 16
    for rate in PROTOCOL["probe_rates"]:
        enough_time()
        name = f"probe-{rate}"
        w = prepare(
            args.output / "workloads" / name,
            "low_reuse",
            404,
            PROTOCOL["probe_calls"],
            rate,
            config.model,
        )
        s = await measured(
            w, config, "least_loaded", args.output / "probes" / name, vocabulary
        )
        probes.append({"rate": rate, **s})
        print(json.dumps({"probe_rate": rate, "p95_ms": s["p95_ttft_ms"]}), flush=True)
        if s["p95_ttft_ms"] >= PROTOCOL["probe_p95_threshold_ms"]:
            selected = rate
            break
    _atomic_json(
        args.output / "selection.json",
        {"rate": selected, "calls_per_run": PROTOCOL["study_calls"], "probes": probes},
    )
    for si, seed in enumerate(SEEDS):
        conditions = CONDITIONS[si:] + CONDITIONS[:si]
        for ci, condition in enumerate(conditions):
            name = f"{condition}-s{seed}"
            await warmup("warmup-" + name)
            w = prepare(
                args.output / "workloads" / name,
                condition,
                seed,
                PROTOCOL["study_calls"],
                selected,
                config.model,
            )
            policies = (
                ("least_loaded", "cache_max")
                if (si * 3 + ci) % 2 == 0
                else ("cache_max", "least_loaded")
            )
            for policy in policies:
                enough_time()
                print(
                    json.dumps(
                        {
                            "starting": name,
                            "policy": policy,
                            "utc": datetime.now(UTC).isoformat(),
                        }
                    ),
                    flush=True,
                )
                s = await measured(
                    w,
                    config,
                    policy,
                    args.output / "runs" / (name + "-" + policy),
                    vocabulary,
                )
                print(
                    json.dumps(
                        {"finished": name, "policy": policy, "p95_ms": s["p95_ttft_ms"]}
                    ),
                    flush=True,
                )
    _atomic_json(args.output / "complete.json", {"runs": 18, "rate": selected})


def report(root, output):
    rows = []
    paired_identities = {}
    deployment_identity = None
    for path in sorted((root / "runs").iterdir()):
        if not path.is_dir():
            continue
        m = validate_completed_run(
            path, required_outputs=("summary.json", "requests.jsonl")
        )
        s = json.loads((path / "summary.json").read_text())
        scenario = json.loads((path / "resolved-scenario.json").read_text())
        deployment = m["outputs"]["gpu-config.json"]["sha256"]
        if deployment_identity is not None and deployment != deployment_identity:
            raise ValueError("study deployment differs across runs")
        deployment_identity = deployment
        pair = (scenario["name"], scenario["seed"])
        identity = m["outputs"]["token-workload.json"]["sha256"]
        if pair in paired_identities and paired_identities[pair] != identity:
            raise ValueError("paired workloads differ")
        paired_identities[pair] = identity
        reqs = [
            json.loads(l) for l in (path / "requests.jsonl").read_text().splitlines()
        ]
        after = json.loads(
            (path.parent / (path.name + "-final-metrics.json")).read_text()
        )
        hits = queries = 0
        for i, a in enumerate(after):
            b = metrics((path / f"worker-{i}-before.prom").read_text())
            for name in ("vllm:prefix_cache_hits_total", "vllm:prefix_cache_hits"):
                if name in a:
                    hits += a[name] - b.get(name, 0)
            for name in (
                "vllm:prefix_cache_queries_total",
                "vllm:prefix_cache_queries",
            ):
                if name in a:
                    queries += a[name] - b.get(name, 0)
        telemetry = [
            json.loads(l)
            for l in (path.parent / (path.name + "-telemetry.jsonl"))
            .read_text()
            .splitlines()
        ]
        rows.append(
            {
                "condition": scenario["name"],
                "seed": scenario["seed"],
                "policy": m["policy"],
                **s,
                "actual_cache_hits": hits,
                "actual_cache_queries": queries,
                "actual_cache_hit_fraction": hits / queries if queries > 0 else None,
                "worker_calls": [
                    sum(r.get("worker_id") == str(i) for r in reqs) for i in range(2)
                ],
                "peak_waiting": [
                    max(
                        (
                            t.get("workers", [{}, {}])[i].get(
                                "vllm:num_requests_waiting", 0
                            )
                            for t in telemetry
                        ),
                        default=0,
                    )
                    for i in range(2)
                ],
            }
        )
    output.mkdir(parents=True, exist_ok=True)
    _atomic_json(output / "results.json", rows)
    lines = [
        "# Controlled GPU prefix-affinity study",
        "",
        "Synthetic 4096-token prompts, 128-token outputs, two independent Qwen3-4B workers. Three seeds; fixed load selected by a separate probe. TTFT means scheduled release to first visible output. Individual repetitions are shown; no significance claim.",
        "",
        "| Condition | Seed | Policy | Calls | p95 first output ms | p95 completion ms | Cache hit % | Worker calls | Peak waiting |",
        "|---|---:|---|---:|---:|---:|---:|---|---|",
    ]
    for r in rows:
        hit = (
            "unavailable"
            if r["actual_cache_hit_fraction"] is None
            else f"{100 * r['actual_cache_hit_fraction']:.1f}"
        )
        lines.append(
            f"| {r['condition']} | {r['seed']} | {r['policy']} | {r['successful_calls']}/{r['calls']} | {r['p95_ttft_ms']:.1f} | {r['p95_latency_ms']:.1f} | {hit} | {r['worker_calls']} | {r['peak_waiting']} |"
        )
    lines += [
        "",
        "Cache fractions are differences of server token counters, not router estimates. Peak queues are sampled approximately once per second. Tenant labels use identical request lengths here; this does not test heterogeneous agent behavior. Warm-up precedes each pair; caches reset before each policy. Three seeds provide limited repeatability evidence. All attempted runs, including probe and warm-up runs, are retained.",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    _atomic_json(
        output / "manifest.json",
        {
            "outputs": _checksums(
                [
                    p
                    for p in output.iterdir()
                    if p.is_file() and p.name != "manifest.json"
                ],
                output,
            )
        },
    )
    print(f"Reported {len(rows)} runs to {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stop-at")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report:
        report(args.report, args.output)
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
