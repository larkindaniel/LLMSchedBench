# LLMSchedBench bounded simulation report

Generated: 2026-08-11T06:18:48.645515Z

## Scope and completion

This report contains 12 of 28 planned immutable simulator runs. The primary load matrix uses seed 1729 and is reported separately from the five-seed 1.6-arrivals/s confidence-interval slice.

## Single-seed load matrix

These values are descriptive single-seed results; they are not confidence intervals.

| Load | Policy | p95 TTFT (ms) | p95 latency (ms) | SLO attainment | Prefix hit | Call goodput/s | Jain fairness | Horizon starvation | NPU util. |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | least_loaded | 266.09 | 12792.65 | 100.00% | 52.25% | 1.300 | 0.604 | 23.53% | 84.74% |
| 1 | cache_max | 271.24 | 12380.39 | 100.00% | 63.92% | 1.350 | 0.588 | 20.59% | 74.87% |
| 1 | weighted_fair | 266.09 | 12792.65 | 100.00% | 52.25% | 1.300 | 0.604 | 23.53% | 84.74% |
| 1 | slo_guarded_affinity | 271.24 | 12380.39 | 100.00% | 63.92% | 1.350 | 0.588 | 20.59% | 74.87% |
| 1.6 | least_loaded | 253.77 | 12154.71 | 100.00% | 54.51% | 1.600 | 0.746 | 31.03% | 89.42% |
| 1.6 | cache_max | 225.85 | 12378.83 | 100.00% | 63.92% | 1.760 | 0.673 | 26.67% | 86.56% |
| 1.6 | weighted_fair | 253.77 | 12154.71 | 100.00% | 54.51% | 1.600 | 0.746 | 31.03% | 89.42% |
| 1.6 | slo_guarded_affinity | 225.85 | 12378.83 | 100.00% | 63.92% | 1.760 | 0.673 | 26.67% | 86.56% |
| 2.2 | least_loaded | 292.19 | 12011.83 | 100.00% | 50.82% | 1.760 | 0.571 | 40.74% | 87.85% |
| 2.2 | cache_max | 226.45 | 12299.39 | 100.00% | 63.92% | 1.760 | 0.567 | 42.86% | 72.78% |
| 2.2 | weighted_fair | 292.19 | 12011.83 | 100.00% | 50.82% | 1.760 | 0.571 | 40.74% | 87.85% |
| 2.2 | slo_guarded_affinity | 226.45 | 12299.39 | 100.00% | 63.92% | 1.760 | 0.567 | 42.86% | 72.78% |

![Single-seed p95 TTFT](figures/load-sweep.svg)

## Five-seed confidence intervals at 1.6 arrivals/s

Intervals are two-sided 95% Student-t intervals across deterministic workload seeds. A row with n<5 is preliminary and is not a completed milestone interval.

| Policy | Seeds | p95 TTFT mean [95% CI] ms | SLO attainment mean [95% CI] | Prefix hit mean [95% CI] | Goodput mean [95% CI] |
|---|---:|---:|---:|---:|---:|
| least_loaded | 1 | 253.77 [253.77, 253.77] | 100.00 [100.00, 100.00]% | 54.51 [54.51, 54.51]% | 1.60 [1.60, 1.60] |
| cache_max | 1 | 225.85 [225.85, 225.85] | 100.00 [100.00, 100.00]% | 63.92 [63.92, 63.92]% | 1.76 [1.76, 1.76] |
| weighted_fair | 1 | 253.77 [253.77, 253.77] | 100.00 [100.00, 100.00]% | 54.51 [54.51, 54.51]% | 1.60 [1.60, 1.60] |
| slo_guarded_affinity | 1 | 225.85 [225.85, 225.85] | 100.00 [100.00, 100.00]% | 63.92 [63.92, 63.92]% | 1.76 [1.76, 1.76] |

![Five-seed p95 TTFT](figures/ci-slice.svg)

## Metric definitions

- TTFT and completion latency percentiles are calculated over simulator call rows and reported per tenant in the machine-readable summaries.
- SLO attainment means TTFT is at or below the scenario-defined tenant SLO; the SLOs are controlled assumptions, not production measurements.
- Call goodput counts SLO-attaining calls completed during the fixed offered-load window, divided by that window's duration. Agent sessions can contribute multiple dependent calls.
- Prefix hit rate is the simulator's avoided prompt-token count divided by total input tokens and is cross-checked against routing-decision cache estimates.
- Weighted service fairness is Jain's index over per-tenant completed token service divided by scenario tenant weight within the fixed offered-load window.
- Horizon starvation is the share of calls released by the end of the fixed offered-load window that have not completed by that horizon. Severe SLO starvation (TTFT over 5× SLO) is retained separately in JSON.
- NPU utilization uses the benchmark cluster's neutral 0/0/1 power meter, where active NPU-seconds divided by simulated duration and worker count equals utilization.

## Reproducibility and limitations

Every summary records the run-manifest, workload, decision-log, and simulator-result checksums. Raw runs remain outside Git because they are reproducible and bulky; compact summaries, figures, and this report are intended for version control. Results are simulator measurements under Linux AMD64 emulation on Apple Silicon, not real-GPU validation. The bounded milestone covers the controlled 60/25/15 base mix only; agent-burst, low-prefix-reuse, fairness/affinity ablations, and real vLLM validation remain future work.
