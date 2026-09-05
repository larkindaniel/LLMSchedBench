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
- [x] Complete the bounded 12-run, three-load/four-policy seed-1729 matrix.
- [x] Run the four policies across five deterministic seeds at 1.6 arrivals/s.
- [x] Calculate latency, TTFT, SLO, cache, fairness, starvation, utilization,
  goodput, worker-variance, and agent-workflow metrics.
- [x] Generate two-sided 95% Student-t intervals, static SVG figures, and the
  traceable technical report.
- [ ] Expand to the deferred 0.5× / 0.8× / 1.1× scenario matrix and explicit
  fairness/affinity, low-prefix-reuse, and agent-burst ablations.

## Milestone 4: real-system validation

- [x] Provision two identical GPU workers within a bounded rental window.
- [x] Validate the real vLLM backend on Qwen3-4B and retain pinned configurations.
- [x] Complete the 18-run controlled prefix-affinity study on two A6000 workers.
- [x] Include figures, request logs, source snapshot, protocol, and findings in the repository.
- [ ] Expand from fixed-length synthetic controls to longer heterogeneous trace runs.
- [ ] Compare qualitative ranking and crossover behavior with a matched simulator setup.
- [ ] Test load sweeps, cache-capacity constraints, and cache-disabled ablations.

See [the hardware findings](GPU_AFFINITY_FINDINGS.md) for the measured results and
limits; the popular-prefix bottleneck was not observed in the tested configuration.
