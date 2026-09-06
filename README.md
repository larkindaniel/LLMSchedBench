# LLMSchedBench

A reproducible benchmark for studying **prefix-cache reuse, queueing, and LLM
request routing** across inference workers. Replay controlled workloads in
[LLMServingSim](https://github.com/casys-kaist/LLMServingSim) or against real
[vLLM](https://github.com/vllm-project/vllm) GPU servers, compare routing policies,
and inspect request-level measurements and server telemetry.

## Real-GPU results

**Prefix sharing affected latency much more than routing-policy choice.**
A controlled study compared least-loaded and cache-aware routing on two NVIDIA
A6000 workers serving Qwen3-4B: **18 runs, three workload seeds, and 3,456 measured
requests with zero failures**.

Each run used 4,096-token inputs, 128-token outputs, and a nominal arrival rate
of four requests per second. Values below are means of three per-run p95
first-output latencies.

| Prefix pattern | Least-loaded | Cache-Max |
|---|---:|---:|
| Little reuse | 11,443 ms | 11,276 ms |
| Distributed reuse | 598 ms | 572 ms |
| Popular prefix | 514 ms | 492 ms |

![Prefix reuse and routing performance on real GPUs](artifacts/gpu-affinity/figures/prefix-affinity.png)

Shared-prefix workloads had roughly 95% lower first-output latency than the
unique-prefix control under both policies. Cache-Max provided smaller additional
improvements. The expected popular-prefix queueing bottleneck did not appear:
requests remained nearly evenly split between workers.

These results cover one model, one selected load, and short synthetic workloads;
three seeds do not establish a general policy ranking. First-output timing
includes client scheduling lag.

[Findings and limitations](docs/GPU_AFFINITY_FINDINGS.md) ·
[Experiment protocol](docs/GPU_AFFINITY_STUDY.md) ·
[Measured results](artifacts/gpu-affinity/report.md)

## Routing policies

| Policy | Strategy | Backend |
|---|---|---|
| `least_loaded` | Select the worker with the least queued work. | Simulator, GPU |
| `cache_max` | Maximize estimated prefix reuse, breaking ties by load. | Simulator, GPU |
| `weighted_fair` | Order requests by tenant-weighted service, then route by load. | Simulator |
| `slo_guarded_affinity` | Prefer a warm-cache worker when predicted time to first token meets the SLO. | Simulator |

The C++20 policy engine is exposed to Python through pybind11. Python handles
workload generation, replay, measurement, and reporting. Runs retain inputs,
routing decisions, measurements, and checksums for reproducibility.

## Simulator benchmark

The published simulator experiment contains 28 runs mixing chat, coding-agent,
and API/batch traffic on two simulated RTX PRO 6000 workers serving Llama 3.1 8B.
It measures latency, prefix reuse, SLO goodput, fairness, and utilization.
Cache-oriented policies improved mean latency and reuse, but overlapping
five-seed confidence intervals leave the policy ranking inconclusive.

The simulator and hardware studies use different models and workloads, so their
absolute performance is not directly comparable.

[Simulator results and confidence intervals](artifacts/overnight-m3/report.md)

## Quick start

Requires Python 3.11, CMake 3.24+, and a C++20 compiler. Simulator runs also
require Docker with Linux AMD64 support.

```bash
git clone --recurse-submodules https://github.com/larkindaniel/LLMSchedBench.git
cd LLMSchedBench
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

# Validate the environment and run checks
./scripts/reproduce-headline.sh --preflight-only

# Reproduce the 28-run simulator benchmark
./scripts/reproduce-headline.sh
```

The reproduction workflow prepares data, calibrates the simulator, and generates
a report. Completed runs are validated and reused. Allow several hours on Apple
Silicon due to AMD64 emulation.

For hardware experiments, follow the [vLLM setup guide](docs/REAL_GPU.md) and
[controlled-study protocol](docs/GPU_AFFINITY_STUDY.md).

## Documentation

- [Workload scenarios and data schema](docs/DATA_SCHEMA.md)
- [Example scenario](scenarios/balanced.yaml)
- [GPU measurements and evidence manifest](artifacts/gpu-affinity/manifest.json)
- [Roadmap](docs/ROADMAP.md)
