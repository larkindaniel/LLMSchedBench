# Implementation roadmap

## Milestone 1: reproducible data path

- [x] Pin and reproduce LLMServingSim's included chat and agentic examples.
- [x] Add checksummed Qwen-Bailian and TraceLab fetch manifests.
- [x] Implement source-specific adapters and malformed-row diagnostics.
- [x] Generate canonical Parquet plus deterministic LLMServingSim input.
- [x] Produce and test a small three-tenant mixed trace.

## Milestone 2: policy engine and simulator bridge

- [x] Complete C++ unit-test integration.
- [x] Implement cache-max, weighted-fair, and SLO-guarded-affinity.
- [x] Calibrate prefill and decode service rates from unloaded runs.
- [x] Add the minimal custom-routing compatibility patch.
- [x] Verify external least-loaded matches built-in LOAD.

## Milestone 3: experiments and report

- [x] Locate the initial smoke-mix `R_sat` (2.0 arrivals/s at 90.05% average
  simulated NPU utilization).
- [ ] Compile the fixed 0.5× / 0.8× / 1.1× load and scenario matrix.
- Run five deterministic seeds and policy ablations.
- Calculate latency, cache, fairness, goodput, and load metrics.
- Generate confidence intervals and the static technical report.

## Milestone 4: real-system validation

- Provision two identical 24 GB-or-larger GPU workers within the approved cap.
- Profile Qwen3-4B and replay timestamp-faithful Qwen traffic.
- Compare qualitative ranking and crossover behavior with simulation.
- Record exact cloud configuration and final reproduction commands.
